from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import Batch, File as FileModel
from schemas import BatchCreate, BatchOut

router = APIRouter()


@router.post("/batches")
def create_batch(req: BatchCreate, db: Session = Depends(get_db)):
    input_file = db.query(FileModel).filter(FileModel.id == req.input_file_id).first()
    if not input_file:
        raise HTTPException(status_code=400, detail=f"Input file '{req.input_file_id}' not found")

    total_requests = 0
    with open(input_file.filepath, "r") as f:
        for line in f:
            if line.strip():
                total_requests += 1

    batch = Batch(
        endpoint=req.endpoint,
        input_file_id=req.input_file_id,
        completion_window=req.completion_window,
        status="validating",
        request_counts_total=total_requests,
    )
    db.add(batch)
    db.commit()
    db.refresh(batch)
    return BatchOut.from_batch(batch)


@router.get("/batches/{batch_id}")
def get_batch(batch_id: str, db: Session = Depends(get_db)):
    batch = db.query(Batch).filter(Batch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    return BatchOut.from_batch(batch)


@router.get("/batches")
def list_batches(db: Session = Depends(get_db)):
    batches = db.query(Batch).order_by(Batch.created_at.desc()).all()
    return {"object": "list", "data": [BatchOut.from_batch(b) for b in batches]}
