from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session
from database import get_db
from models import (
    Batch, BatchAssignment, File as FileModel, Worker,
    WorkerGpu, WorkerRuntime, RuntimeModel, unix_now,
    FinetuneJob, FinetuneJobAssignment, FinetuneArtifact,
)
from schemas import (
    ModelDownloadReport, ProgressReport,
    WorkerHeartbeatRequest, WorkerRegisterRequest,
    FinetuneProgressReport,
)
from pydantic import BaseModel
from typing import Optional
from auth import get_worker_context
from provider_picker import picker, get_catalog_entry
from finetune_picker import finetune_picker
from finetune_contract import ALL_LINEAR, ENGINE, ENGINE_IMAGE, adapter_unservable_reasons
from services.finetune_events import InvalidTransition, emit_event, transition
from sweeper import (
    MAX_BATCH_ATTEMPTS, requeue_or_fail_batch, requeue_or_fail_finetune_job,
)
import hashlib, json, shutil, os, logging, zipfile

logger = logging.getLogger(__name__)

router = APIRouter()

VALID_TRANSITIONS = {
    "validating":  ["validated", "failed"],
    "validated":   ["in_progress"],
    "in_progress": ["completed", "failed", "validated"],  # "validated" = requeue
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

    cpu_cores = req.cpu.get("cores") if req.cpu else None
    ram_gb = req.ram.get("total_gb") if req.ram else None

    def _gpu_rows():
        return [
            WorkerGpu(
                gpu_index=g.index, vendor=g.vendor, name=g.name,
                vram_gb=g.vram_gb, driver=g.driver, cuda=g.cuda,
            )
            for g in req.gpus
        ]

    def _runtime_rows():
        return [
            WorkerRuntime(
                engine=r.type,
                base_url=r.endpoint,
                models=[
                    RuntimeModel(
                        name=m, runtime_model_id=m,
                        digest=(r.model_digests or {}).get(m),
                    )
                    for m in r.models
                ],
            )
            for r in req.runtimes
        ]

    if existing:
        existing.api_key_id = _api_key.id
        existing.os = req.os
        existing.cpu_cores = cpu_cores
        existing.ram_total_gb = ram_gb
        # Replace-all: clear + flush first so the old rows' DELETEs hit the
        # DB before the new INSERTs (unique constraints would trip otherwise)
        existing.gpus = []
        existing.runtimes = []
        db.flush()
        existing.gpus = _gpu_rows()
        existing.runtimes = _runtime_rows()
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
        api_key_id=_api_key.id,
        hostname=req.hostname,
        os=req.os,
        cpu_cores=cpu_cores,
        ram_total_gb=ram_gb,
        gpus=_gpu_rows(),
        runtimes=_runtime_rows(),
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

    Updates liveness (`status`/`last_heartbeat`), the daemon-reported
    `activity`, and the dynamic capability data (VRAM, loaded models)
    that the provider picker matches on during /workers/poll.
    """
    _api_key, org = ctx

    worker = _get_org_worker(db, org, worker_id)

    # Heartbeats prove liveness but must not undo an operator's drain
    # (spec §15: drain = finish current batch, take no more).
    if worker.status != "draining":
        worker.status = "online"
    worker.activity = req.activity
    worker.last_heartbeat = unix_now()
    worker.vram_total_gb = req.vram_total_gb
    worker.vram_available_gb = req.vram_available_gb

    # Map reported loaded models onto runtime_models.loaded flags, and
    # record the digest of each loaded model (the reproducibility pin).
    reported = set(req.loaded_models)
    digests = req.loaded_model_digests or {}
    known = set()
    for runtime in worker.runtimes:
        for model in runtime.models:
            was_loaded = model.loaded
            model.loaded = model.name in reported
            if model.name in digests and digests[model.name]:
                model.digest = digests[model.name]
            if model.loaded != was_loaded:
                model.updated_at = unix_now()
            known.add(model.name)
    # A loaded model we've never seen (e.g. pulled on the fly): record it.
    # The daemon runs a single runtime, so attach to the first one.
    missing = reported - known
    if missing and worker.runtimes:
        for name in missing:
            worker.runtimes[0].models.append(
                RuntimeModel(
                    name=name, runtime_model_id=name,
                    digest=digests.get(name), loaded=True,
                )
            )

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

    # A draining worker finishes its current batch but takes nothing new.
    if worker.status == "draining":
        return {"job": None}

    available_batches = (
        db.query(Batch)
        .filter(Batch.status == "validated")
        .order_by(Batch.created_at)
        .all()
    )

    if not available_batches:
        return {"job": None}

    # Catalogue-aware matching (VRAM fit + hosts the artifact), handling
    # both the heartbeated and never-heartbeated worker inside the picker.
    batch = picker.find_best_batch(db, worker, available_batches)

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

    # The daemon runs the runtime's own model id, not our catalogue slug.
    entry = get_catalog_entry(db, batch.model)
    runtime_model_id = entry.runtime_model_id if entry else batch.model

    return {
        "job": {
            "job_id":        batch.id,
            "input_file_id": batch.input_file_id,
            "input_path":    f"/v1/files/{batch.input_file_id}/content",
            "model":         runtime_model_id,
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


# ─── Finetune Job Polling & Results ────────────────────────
#
# Deliberately separate endpoints from the batch ones above rather than
# branching /workers/poll — training jobs have a different queue, a
# different fit function (finetune_picker: VRAM headroom for training,
# not "already loaded"), and a different payload shape (hyperparameters,
# not a runtime model id). Keeping them apart means the well-exercised
# batch poll path is untouched by this vertical. Statuses and limits are
# finetune_contract.py's.

class FinetunePollRequest(BaseModel):
    worker_id: str


class FinetuneFailureReport(BaseModel):
    job_id: str
    worker_id: str
    error: Optional[str] = None
    # The runner classifies the engine's failure: `code` is machine-readable
    # (e.g. gpu_out_of_memory), `error` is the message the user sees.
    # retryable=False fails the job now — a bad config or dataset fails the
    # same way on every attempt, so requeueing only wastes GPU time.
    code: Optional[str] = None
    retryable: bool = True


def _get_assigned_finetune_job(db: Session, job_id: str, worker_id: str) -> FinetuneJob:
    """Resolve a finetune job currently assigned to this worker, else 404/403."""
    job = db.query(FinetuneJob).filter(FinetuneJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Finetune job not found")

    assignment = db.query(FinetuneJobAssignment).filter(
        FinetuneJobAssignment.job_id == job_id,
    ).first()
    if not assignment or assignment.worker_id != worker_id:
        raise HTTPException(
            status_code=403,
            detail="Finetune job is not assigned to this worker",
        )
    return job


def _ft_transition(db: Session, job: FinetuneJob, target: str, message: str, **kw) -> None:
    try:
        transition(db, job, target, message, **kw)
    except InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/finetune-poll")
def poll_finetune_job(
    req: FinetunePollRequest,
    ctx=Depends(get_worker_context),
    db: Session = Depends(get_db),
):
    _api_key, org = ctx
    worker = _get_org_worker(db, org, req.worker_id)

    if worker.status == "draining":
        return {"job": None}

    available_jobs = (
        db.query(FinetuneJob)
        .filter(FinetuneJob.status == "queued")
        .order_by(FinetuneJob.created_at)
        .all()
    )
    if not available_jobs:
        return {"job": None}

    job = finetune_picker.find_best_job(db, worker, available_jobs)
    if not job:
        return {"job": None}

    _ft_transition(db, job, "running", f"Training started on worker {worker.id}")
    db.add(FinetuneJobAssignment(
        job_id=job.id,
        worker_id=req.worker_id,
        assigned_at=unix_now(),
    ))
    db.commit()
    db.refresh(job)

    target_modules = job.target_modules
    return {
        "job": {
            "job_id":                    job.id,
            "method":                    job.method,
            "dataset_file_id":           job.training_file_id,
            "dataset_path":              f"/v1/files/{job.training_file_id}/content",
            "validation_file_id":        job.validation_file_id,
            "base_model_id":             job.base_model_id,
            "source_ref":                job.base_source_ref,
            "source_revision":           job.base_source_revision,
            "lora_rank":                 job.lora_rank,
            "lora_alpha":                job.lora_alpha,
            "lora_dropout":              job.lora_dropout,
            # Comma-separated for the daemon's existing str field; "all-linear" as-is.
            "target_modules":            target_modules if target_modules == ALL_LINEAR
                                         else ",".join(json.loads(target_modules)),
            "epochs":                    job.n_epochs,
            "batch_size":                job.batch_size,
            "learning_rate":             job.learning_rate,
            "max_seq_length":            job.max_seq_length,
            "seed":                      job.seed,
            "engine":                    ENGINE,
            "engine_image":              ENGINE_IMAGE,
            # Restarting from step 0 today; checkpoint artifacts are where
            # resume will come from (see finetune_artifacts.kind).
            "resume_from_checkpoint_step": None,
        }
    }


@router.post("/finetune-progress")
def report_finetune_progress(
    req: FinetuneProgressReport,
    ctx=Depends(get_worker_context),
    db: Session = Depends(get_db),
):
    """One trainer log step from a worker -> one `metrics` event. Answers
    {"stop": true} once the job is cancelled, so the runner can kill the
    engine instead of training to completion."""
    _api_key, org = ctx
    _get_org_worker(db, org, req.worker_id)
    job = _get_assigned_finetune_job(db, req.job_id, req.worker_id)

    if job.status != "running":
        return {"status": job.status, "job_id": job.id, "stop": True}

    metrics = req.model_dump(exclude={"job_id", "worker_id"}, exclude_none=True)
    loss = f", loss={req.loss:.4f}" if req.loss is not None else ""
    if req.eval_loss is not None:
        loss += f", eval_loss={req.eval_loss:.4f}"
    emit_event(db, job.id, f"Step {req.current_step}/{req.total_steps}{loss}",
               type="metrics", data=metrics)
    db.commit()

    return {"status": "ok", "job_id": job.id, "stop": False}


def _read_adapter_zip(path: str) -> tuple[dict, str]:
    """(adapter_config, sha256 of adapter_model.safetensors) from an adapter zip.
    Files may sit at the zip root or under one directory."""
    with zipfile.ZipFile(path) as zf:
        names = {n.rsplit("/", 1)[-1]: n for n in zf.namelist()}
        if "adapter_config.json" not in names or "adapter_model.safetensors" not in names:
            raise ValueError("zip must contain adapter_config.json and adapter_model.safetensors")
        config = json.loads(zf.read(names["adapter_config.json"]))
        digest = hashlib.sha256()
        with zf.open(names["adapter_model.safetensors"]) as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                digest.update(chunk)
    return config, digest.hexdigest()


@router.post("/finetune-upload-adapter")
def upload_finetune_adapter(
    job_id: str = Form(...),
    worker_id: str = Form(...),
    file: UploadFile = File(...),
    engine: Optional[str] = Form(None),
    engine_version: Optional[str] = Form(None),
    engine_image: Optional[str] = Form(None),
    metrics: Optional[str] = Form(None),  # JSON object, e.g. {"train_loss": ..., "eval_loss": ...}
    ctx=Depends(get_worker_context),
    db: Session = Depends(get_db),
):
    """Upload the trained LoRA adapter (a zip of adapter_config.json +
    adapter_model.safetensors). The backend reads the adapter's identity
    from the zip itself and records a finetune_artifacts row; the job
    succeeds only if the adapter is servable (finetune_contract)."""
    _api_key, org = ctx
    _get_org_worker(db, org, worker_id)
    job = _get_assigned_finetune_job(db, job_id, worker_id)
    if job.status != "running":
        raise HTTPException(status_code=409, detail=f"Job is {job.status}, not running")

    output_file = FileModel(
        user_id=job.user_id,
        filename=f"{job.id}_adapter.zip",
        purpose="fine-tune-results",
    )
    db.add(output_file)
    db.flush()

    FILES_DIR = "files"
    os.makedirs(FILES_DIR, exist_ok=True)
    filepath = f"{FILES_DIR}/{output_file.id}.zip"
    with open(filepath, "wb") as f:
        shutil.copyfileobj(file.file, f)
    output_file.filepath = filepath
    output_file.bytes = os.path.getsize(filepath)

    try:
        adapter_config, sha256 = _read_adapter_zip(filepath)
    except (zipfile.BadZipFile, ValueError, json.JSONDecodeError) as exc:
        db.rollback()
        os.remove(filepath)
        raise HTTPException(status_code=400, detail=f"Not a usable adapter zip: {exc}")

    targets = adapter_config.get("target_modules")
    if isinstance(targets, str):
        targets = [targets]
    reasons = adapter_unservable_reasons(adapter_config, expected_base_ref=job.base_source_ref)
    try:
        metrics_obj = json.loads(metrics) if metrics else None
    except json.JSONDecodeError:
        metrics_obj = {"raw": metrics}

    artifact = FinetuneArtifact(
        job_id=job.id,
        kind="adapter",
        file_id=output_file.id,
        sha256=sha256,
        bytes=output_file.bytes,
        base_model_id=job.base_model_id,
        base_source_ref=job.base_source_ref,
        base_source_revision=job.base_source_revision,
        peft_type=adapter_config.get("peft_type"),
        lora_rank=adapter_config.get("r") if isinstance(adapter_config.get("r"), int) else None,
        lora_alpha=adapter_config.get("lora_alpha") if isinstance(adapter_config.get("lora_alpha"), int) else None,
        target_modules=json.dumps(sorted(targets)) if targets else None,
        modules_to_save=json.dumps(adapter_config["modules_to_save"]) if adapter_config.get("modules_to_save") else None,
        engine=engine,
        engine_version=engine_version,
        engine_image=engine_image,
        metrics=json.dumps(metrics_obj) if metrics_obj is not None else None,
        servable=not reasons,
        unservable_reasons=json.dumps(reasons) if reasons else None,
    )
    db.add(artifact)
    db.flush()

    if reasons:
        job.error = json.dumps({
            "code": "unservable_adapter",
            "param": None,
            "message": "The trained adapter cannot be served: " + "; ".join(reasons),
        })
        _ft_transition(db, job, "failed",
                       f"Adapter {artifact.id} is not servable: {'; '.join(reasons)}", level="error")
    else:
        suffix = f"{job.suffix}:" if job.suffix else ""
        job.fine_tuned_model = f"ft:{job.base_model_id}:{suffix}{job.id.removeprefix('ftjob-')[:12]}"
        stats = json.loads(job.training_file_stats) if job.training_file_stats else {}
        if stats.get("total_tokens"):
            job.trained_tokens = stats["total_tokens"] * job.n_epochs
        _ft_transition(db, job, "succeeded",
                       f"Training succeeded; adapter {artifact.id} is servable as {job.fine_tuned_model}")

    db.query(FinetuneJobAssignment).filter(FinetuneJobAssignment.job_id == job.id).delete()
    db.commit()
    db.refresh(job)

    return {
        "status":                 job.status,
        "job_id":                 job.id,
        "artifact_id":            artifact.id,
        "output_adapter_file_id": output_file.id,
        "servable":               not reasons,
        "unservable_reasons":     reasons,
        "fine_tuned_model":       job.fine_tuned_model,
    }


@router.post("/finetune-report-failure")
def report_finetune_failure(
    req: FinetuneFailureReport,
    ctx=Depends(get_worker_context),
    db: Session = Depends(get_db),
):
    """Runner reports a training failure. Retryable failures requeue until
    MAX_FINETUNE_ATTEMPTS (each retry restarts from step 0, and the event
    log says so); non-retryable ones fail the job immediately."""
    _api_key, org = ctx
    _get_org_worker(db, org, req.worker_id)
    job = _get_assigned_finetune_job(db, req.job_id, req.worker_id)

    if job.status != "running":
        db.query(FinetuneJobAssignment).filter(FinetuneJobAssignment.job_id == job.id).delete()
        db.commit()
        return {"status": job.status, "job_id": job.id, "attempts": job.attempts, "error": req.error}

    outcome = requeue_or_fail_finetune_job(db, job, error=req.error, code=req.code,
                                           retryable=req.retryable)

    db.commit()
    db.refresh(job)

    return {
        "status":   outcome,
        "job_id":   job.id,
        "attempts": job.attempts,
        "error":    req.error,
    }
