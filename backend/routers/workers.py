from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session
from database import get_db
from models import Batch, BatchAssignment, File as FileModel, ProviderCapability, unix_now
from schemas import HeartbeatRequest
from pydantic import BaseModel
from typing import Optional
from auth import require_role
from provider_picker import picker
import shutil, os, json

router = APIRouter()

VALID_TRANSITIONS = {
    "validating":  ["validated", "failed"],
    "validated":   ["in_progress"],
    "in_progress": ["completed", "failed"],
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


@router.post("/heartbeat")
def heartbeat(
    req: HeartbeatRequest,
    user=Depends(require_role("provider", "admin")),
    db: Session = Depends(get_db),
):
    caps = db.query(ProviderCapability).filter(
        ProviderCapability.worker_id == req.worker_id,
    ).first()

    if caps:
        caps.vram_total_gb = req.vram_total_gb
        caps.vram_available_gb = req.vram_available_gb
        caps.loaded_models = json.dumps(req.loaded_models)
        caps.status = "online"
        caps.last_heartbeat = unix_now()
    else:
        caps = ProviderCapability(
            worker_id=req.worker_id,
            provider_id=user.id,
            vram_total_gb=req.vram_total_gb,
            vram_available_gb=req.vram_available_gb,
            loaded_models=json.dumps(req.loaded_models),
            status="online",
            last_heartbeat=unix_now(),
        )
        db.add(caps)

    db.commit()
    return {"status": "ok"}


@router.post("/poll")
def poll_job(
    req: PollRequest,
    user=Depends(require_role("provider", "admin")),
    db: Session = Depends(get_db),
):
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
        batch = available_batches[0] if available_batches else None

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


@router.post("/upload-results")
def upload_results(
    job_id: str = Form(...),
    file: UploadFile = File(...),
    user=Depends(require_role("provider", "admin")),
    db: Session = Depends(get_db),
):
    batch = db.query(Batch).filter(Batch.id == job_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")

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
    batch.request_counts_completed = batch.request_counts_total

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
    user=Depends(require_role("provider", "admin")),
    db: Session = Depends(get_db),
):
    batch = db.query(Batch).filter(Batch.id == req.job_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")

    validate_transition(batch.status, "failed")
    batch.status = "failed"
    batch.completed_at = unix_now()
    batch.request_counts_failed = batch.request_counts_total

    db.commit()
    db.refresh(batch)

    return {
        "status":   "failed",
        "batch_id": batch.id,
        "error":    req.error,
    }