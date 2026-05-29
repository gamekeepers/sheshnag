from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session
from database import get_db
from models import Job, JobAssignment
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timezone

router = APIRouter()

VALID_TRANSITIONS = {
    "queued":    ["running"],
    "running":   ["completed", "failed"],
    "completed": [],            
    "failed":    [],             
}


def validate_transition(current: str, target: str):
    """Raise 409 if the transition is not allowed."""
    allowed = VALID_TRANSITIONS.get(current, [])
    if target not in allowed:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot transition from '{current}' to '{target}'. "
                   f"Allowed transitions: {allowed or 'none (terminal state)'}",
        )

class PollRequest(BaseModel):
    worker_id: str


class FailureReport(BaseModel):
    job_id: str
    worker_id: str
    error: Optional[str] = None

@router.post("/poll")
def poll_job(req: PollRequest, db: Session = Depends(get_db)):
    job = (
        db.query(Job)
        .filter(Job.status == "queued")
        .order_by(Job.created_at)
        .first()
    )

    if not job:
        return {"job": None}

    validate_transition(job.status, "running")
    job.status = "running"

    assignment = JobAssignment(
        job_id=job.id,
        worker_id=req.worker_id,
        assigned_at=datetime.now(timezone.utc),
    )
    db.add(assignment)
    db.commit()
    db.refresh(job)

    return {
        "job": {
            "job_id":     job.id,
            "input_path": job.input_path,
        }
    }


@router.post("/upload-results")
def upload_results(
    job_id: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    import shutil, os

    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    validate_transition(job.status, "completed")

    OUTPUT_DIR = "outputs"
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    output_path = f"{OUTPUT_DIR}/{job_id}_outputs.jsonl"
    with open(output_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    job.output_path = output_path
    job.status = "completed"
    db.commit()
    db.refresh(job)

    return {"status": "completed", "job_id": job.id, "output_path": output_path}


@router.post("/report-failure")
def report_failure(req: FailureReport, db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.id == req.job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    validate_transition(job.status, "failed")
    job.status = "failed"
    db.commit()
    db.refresh(job)

    return {
        "status":    "failed",
        "job_id":    job.id,
        "error":     req.error,
    }