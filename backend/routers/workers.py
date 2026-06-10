from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session
from database import get_db
from models import Batch, BatchAssignment, File as FileModel, unix_now
from pydantic import BaseModel
from typing import Optional
from auth import require_role
import shutil, os

router = APIRouter()

VALID_TRANSITIONS = {
    "validating":  ["in_progress"],
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


@router.post("/poll")
def poll_job(
    req: PollRequest,
    user=Depends(require_role("provider", "admin")),
    db: Session = Depends(get_db),
):
    batch = (
        db.query(Batch)
        .filter(Batch.status == "validating")
        .order_by(Batch.created_at)
        .first()
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