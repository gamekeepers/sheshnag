from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session
from database import get_db
from models import (
    Batch, BatchAssignment, File as FileModel, Worker,
    ProviderCapability, unix_now,
)
from schemas import (
    ModelDownloadReport, ProgressReport,
    WorkerHeartbeatRequest, WorkerRegisterRequest,
)
from pydantic import BaseModel
from typing import Optional
from auth import get_worker_context
from provider_picker import picker
from sweeper import MAX_BATCH_ATTEMPTS, requeue_or_fail_batch
import shutil, os, json, logging

logger = logging.getLogger(__name__)

router = APIRouter()

VALID_TRANSITIONS = {
    "validating":  ["validated", "failed"],
    "validated":   ["in_progress"],
    "in_progress": ["completed", "failed", "validated"],  # "validated" = requeue (spec §12)
    "completed":   [],
    "failed":      [],
}


def validate_transition(current: str, target: str):
    allowed = VALID_TRANSITIONS.get(current, [])
    if target not in allowed:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot transition from '{current}' to '{target}'. "
                   f"Allowed: {allowed or 'none (terminal state)'}",
        )


class PollRequest(BaseModel):
    worker_id: str


class FailureReport(BaseModel):
    job_id: str
    worker_id: str
    error: Optional[str] = None


def _get_org_worker(db: Session, org, worker_id: str) -> Worker:
    """Resolve a worker that belongs to the calling key's org, else 404.

    Every worker endpoint must scope by org — otherwise any org's key
    could act on any worker (spec §17).
    """
    worker = db.query(Worker).filter(
        Worker.id == worker_id,
        Worker.org_id == org.id,
    ).first()
    if not worker:
        raise HTTPException(status_code=404, detail="Worker not found")
    return worker


def _get_assigned_batch(db: Session, job_id: str, worker_id: str) -> Batch:
    """Resolve a batch currently assigned to this worker, else 404/403."""
    batch = db.query(Batch).filter(Batch.id == job_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")

    assignment = db.query(BatchAssignment).filter(
        BatchAssignment.batch_id == job_id,
    ).first()
    if not assignment or assignment.worker_id != worker_id:
        raise HTTPException(
            status_code=403,
            detail="Batch is not assigned to this worker",
        )
    return batch


# ─── Worker Registration & Heartbeat ───────────────────────

@router.post("/register")
def register_worker(
    req: WorkerRegisterRequest,
    ctx=Depends(get_worker_context),
    db: Session = Depends(get_db),
):
    """Register a worker under the API key's organization."""
    _api_key, org = ctx
    org_id = org.id

    # Check if worker with same hostname already exists for this org
    existing = db.query(Worker).filter(
        Worker.org_id == org_id,
        Worker.hostname == req.hostname,
    ).first()

    gpus_json = json.dumps([g.model_dump() for g in req.gpus])
    runtimes_json = json.dumps([r.model_dump() for r in req.runtimes])
    cpu_cores = req.cpu.get("cores") if req.cpu else None
    ram_gb = req.ram.get("total_gb") if req.ram else None

    if existing:
        existing.os = req.os
        existing.cpu_cores = cpu_cores
        existing.ram_total_gb = ram_gb
        existing.gpus = gpus_json
        existing.runtimes = runtimes_json
        existing.status = "online"
        existing.last_heartbeat = unix_now()
        db.commit()
        db.refresh(existing)
        return {
            "worker_id": existing.id,
            "status": "updated",
            "message": f"Worker '{req.hostname}' re-registered",
        }

    worker = Worker(
        org_id=org_id,
        hostname=req.hostname,
        os=req.os,
        cpu_cores=cpu_cores,
        ram_total_gb=ram_gb,
        gpus=gpus_json,
        runtimes=runtimes_json,
    )
    db.add(worker)
    db.commit()
    db.refresh(worker)

    return {
        "worker_id": worker.id,
        "status": "registered",
        "message": f"Worker '{req.hostname}' registered successfully",
    }


@router.post("/{worker_id}/heartbeat")
def worker_heartbeat(
    worker_id: str,
    req: WorkerHeartbeatRequest,
    ctx=Depends(get_worker_context),
    db: Session = Depends(get_db),
):
    """Unified worker heartbeat (spec §8.1).

    Updates the worker's liveness timestamp AND the dynamic capability
    data (VRAM, loaded models) that the provider picker uses to match
    batches to workers during /workers/poll.
    """
    _api_key, org = ctx

    worker = _get_org_worker(db, org, worker_id)

    worker.status = "online"
    worker.last_heartbeat = unix_now()

    caps = db.query(ProviderCapability).filter(
        ProviderCapability.worker_id == worker_id,
    ).first()

    if caps:
        caps.vram_total_gb = req.vram_total_gb
        caps.vram_available_gb = req.vram_available_gb
        caps.loaded_models = json.dumps(req.loaded_models)
        caps.status = "online"
        caps.last_heartbeat = unix_now()
    else:
        caps = ProviderCapability(
            worker_id=worker_id,
            provider_id=org.id,
            vram_total_gb=req.vram_total_gb,
            vram_available_gb=req.vram_available_gb,
            loaded_models=json.dumps(req.loaded_models),
            status="online",
            last_heartbeat=unix_now(),
        )
        db.add(caps)

    db.commit()
    return {"status": "ok", "worker_id": worker_id}


# ─── Job Polling & Results ─────────────────────────────────

@router.post("/poll")
def poll_job(
    req: PollRequest,
    ctx=Depends(get_worker_context),
    db: Session = Depends(get_db),
):
    _api_key, org = ctx
    worker = _get_org_worker(db, org, req.worker_id)

    caps = db.query(ProviderCapability).filter(
        ProviderCapability.worker_id == req.worker_id,
    ).first()

    available_batches = (
        db.query(Batch)
        .filter(Batch.status == "validated")
        .order_by(Batch.created_at)
        .all()
    )

    if not available_batches:
        return {"job": None}

    if caps:
        batch = picker.find_best_batch(caps, available_batches)
    else:
        # No heartbeat yet — fall back to the models the worker advertised
        # at registration. Never hand out an arbitrary batch to a worker
        # whose capabilities are unknown.
        try:
            runtimes = json.loads(worker.runtimes or "[]")
        except json.JSONDecodeError:
            runtimes = []
        advertised = {m for r in runtimes for m in r.get("models", [])}
        batch = next(
            (b for b in available_batches if b.model and b.model in advertised),
            None,
        )

    if not batch:
        return {"job": None}

    validate_transition(batch.status, "in_progress")
    batch.status = "in_progress"

    assignment = BatchAssignment(
        batch_id=batch.id,
        worker_id=req.worker_id,
        assigned_at=unix_now(),
    )
    db.add(assignment)
    db.commit()
    db.refresh(batch)

    return {
        "job": {
            "job_id":        batch.id,
            "input_file_id": batch.input_file_id,
            "input_path":    f"/v1/files/{batch.input_file_id}/content",
            "model":         batch.model,
        }
    }


@router.post("/progress")
def report_progress(
    req: ProgressReport,
    ctx=Depends(get_worker_context),
    db: Session = Depends(get_db),
):
    """Live progress from a worker (every N prompts) — spec §11.

    Keeps the batch's request counts current so the dashboard shows
    real progress instead of 0 until completion.
    """
    _api_key, org = ctx
    _get_org_worker(db, org, req.worker_id)
    batch = _get_assigned_batch(db, req.job_id, req.worker_id)

    batch.request_counts_completed = req.completed
    batch.request_counts_failed = req.failed
    db.commit()

    return {"status": "ok", "batch_id": batch.id}


@router.post("/model-progress")
def report_model_download(
    req: ModelDownloadReport,
    ctx=Depends(get_worker_context),
    db: Session = Depends(get_db),
):
    """Model download progress from a worker.

    Recorded as liveness only for now — whether on-the-fly downloads
    stay (and get a first-class worker status) is an open spec decision.
    """
    _api_key, org = ctx
    worker = _get_org_worker(db, org, req.worker_id)

    worker.last_heartbeat = unix_now()
    db.commit()

    logger.info(
        "Worker %s downloading model %s: %s (%d/%d)",
        req.worker_id, req.model_name, req.status, req.completed, req.total,
    )
    return {"status": "ok", "worker_id": req.worker_id}


@router.post("/upload-results")
def upload_results(
    job_id: str = Form(...),
    worker_id: str = Form(...),
    file: UploadFile = File(...),
    completed: Optional[int] = Form(None),
    failed: Optional[int] = Form(None),
    ctx=Depends(get_worker_context),
    db: Session = Depends(get_db),
):
    _api_key, org = ctx
    _get_org_worker(db, org, worker_id)
    batch = _get_assigned_batch(db, job_id, worker_id)

    validate_transition(batch.status, "completed")

    output_file = FileModel(
        user_id=batch.user_id,
        filename=f"{batch.id}_output.jsonl",
        purpose="batch_output",
    )
    db.add(output_file)
    db.flush()

    FILES_DIR = "files"
    os.makedirs(FILES_DIR, exist_ok=True)
    filepath = f"{FILES_DIR}/{output_file.id}.jsonl"
    with open(filepath, "wb") as f:
        shutil.copyfileobj(file.file, f)

    output_file.filepath = filepath
    output_file.bytes = os.path.getsize(filepath)

    batch.output_file_id = output_file.id
    batch.status = "completed"
    batch.completed_at = unix_now()
    # Real counts from the daemon; if absent, keep whatever the live
    # /workers/progress reports accumulated — never assume 100% success.
    if completed is not None:
        batch.request_counts_completed = completed
    if failed is not None:
        batch.request_counts_failed = failed

    db.commit()
    db.refresh(batch)

    return {
        "status":         "completed",
        "batch_id":       batch.id,
        "output_file_id": output_file.id,
    }


@router.post("/report-failure")
def report_failure(
    req: FailureReport,
    ctx=Depends(get_worker_context),
    db: Session = Depends(get_db),
):
    """Worker reports a job failure — requeue until MAX_BATCH_ATTEMPTS (spec §12)."""
    _api_key, org = ctx
    _get_org_worker(db, org, req.worker_id)
    batch = _get_assigned_batch(db, req.job_id, req.worker_id)

    target = (
        "failed"
        if (batch.attempts or 0) + 1 >= MAX_BATCH_ATTEMPTS
        else "validated"
    )
    validate_transition(batch.status, target)
    outcome = requeue_or_fail_batch(db, batch, error=req.error)

    db.commit()
    db.refresh(batch)

    return {
        "status":   outcome,
        "batch_id": batch.id,
        "attempts": batch.attempts,
        "error":    req.error,
    }