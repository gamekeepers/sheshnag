from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from database import get_db
from models import Job
from schemas import JobOut
import shutil, os

router = APIRouter()
UPLOAD_DIR = "uploads"
OUTPUT_DIR = "outputs"
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

@router.post("/", response_model=JobOut)
def submit_job(file: UploadFile = File(...), db: Session = Depends(get_db)):
    job = Job()
    db.add(job)
    db.flush()  

    file_path = f"{UPLOAD_DIR}/{job.id}_input.jsonl"
    with open(file_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    job.input_path = file_path
    db.commit()
    db.refresh(job)
    return job

@router.get("/", response_model=list[JobOut])
def list_jobs(db: Session = Depends(get_db)):
    return db.query(Job).order_by(Job.created_at.desc()).all()

@router.get("/{job_id}", response_model=JobOut)
def get_job(job_id: str, db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job

@router.get("/{job_id}/outputs")
def download_outputs(job_id: str, db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != "completed" or not job.output_path:
        raise HTTPException(status_code=400, detail="Outputs not ready")
    return FileResponse(job.output_path, filename=f"{job_id}_outputs.jsonl")

@router.get("/{job_id}/input")
def download_input(job_id: str, db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if not job.input_path:
        raise HTTPException(status_code=400, detail="Input file not found")
    return FileResponse(job.input_path, filename=f"{job_id}_input.jsonl")