from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import Batch, File as FileModel
from schemas import BatchCreate, BatchOut, BatchSummary
from auth import get_current_user, require_role
from provider_picker import is_model_supported
import json

router = APIRouter()


@router.post("/batches")
def create_batch(
    req: BatchCreate,
    user=Depends(require_role("user", "admin")),
    db: Session = Depends(get_db),
):
    input_file = db.query(FileModel).filter(FileModel.id == req.input_file_id).first()
    if not input_file:
        raise HTTPException(status_code=400, detail=f"Input file '{req.input_file_id}' not found")

    if user.role == "user" and input_file.user_id != user.id:
        raise HTTPException(status_code=403, detail="You don't own this file")

    model_name = None
    total_requests = 0
    with open(input_file.filepath, "r") as f:
        for line in f:
            if line.strip():
                total_requests += 1
                if model_name is None:
                    try:
                        first_req = json.loads(line)
                        model_name = first_req.get("body", {}).get("model")
                    except json.JSONDecodeError:
                        pass

    if model_name and not is_model_supported(model_name):
        raise HTTPException(
            status_code=400,
            detail=f"Model '{model_name}' is not supported. Check /v1/models for available models.",
        )

    batch = Batch(
        user_id=user.id,
        endpoint=req.endpoint,
        model=model_name,
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
def get_batch(
    batch_id: str,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    batch = db.query(Batch).filter(Batch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")

    if user.role == "user" and batch.user_id != user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    if user.role == "provider":
        return BatchSummary.from_batch(batch)

    return BatchOut.from_batch(batch)


@router.get("/batches")
def list_batches(
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(Batch)

    if user.role == "user":
        query = query.filter(Batch.user_id == user.id)

    batches = query.order_by(Batch.created_at.desc()).all()

    if user.role == "provider":
        return {
            "object": "list",
            "data": [BatchSummary.from_batch(b) for b in batches],
        }

    return {
        "object": "list",
        "data": [BatchOut.from_batch(b) for b in batches],
    }
