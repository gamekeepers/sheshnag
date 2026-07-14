import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from database import get_db
from models import Batch, File as FileModel
from schemas import BatchCreate, BatchOut, BatchSummary
from auth import get_current_user, require_role
from provider_picker import is_model_supported
from services.batch_validator import validate_batch_file
from services.sse_manager import sse_manager

router = APIRouter()


async def _validate_and_notify(batch_id: str, filepath: str) -> None:
    """Run JSONL validation in a background thread, then push an SSE event."""
    result = await asyncio.to_thread(validate_batch_file, batch_id, filepath)
    status = "validated" if result else "failed"
    await sse_manager.publish(
        batch_id,
        "validation_complete",
        {"batch_id": batch_id, "status": status},
    )


@router.post("/batches")
async def create_batch(
    req: BatchCreate,
    user=Depends(require_role("user", "superadmin")),
    db: Session = Depends(get_db),
):
    input_file = db.query(FileModel).filter(FileModel.id == req.input_file_id).first()
    if not input_file:
        raise HTTPException(status_code=400, detail=f"Input file '{req.input_file_id}' not found")

    if user.platform_role == "user" and input_file.user_id != user.id:
        raise HTTPException(status_code=403, detail="You don't own this file")

    batch = Batch(
        user_id=user.id,
        endpoint=req.endpoint,
        input_file_id=req.input_file_id,
        completion_window=req.completion_window,
        status="validating",
        request_counts_total=0,  # set by background validator
    )
    db.add(batch)
    db.commit()
    db.refresh(batch)

    # Fire-and-forget: validate JSONL in a background thread
    asyncio.create_task(_validate_and_notify(batch.id, input_file.filepath))

    return BatchOut.from_batch(batch)


@router.get("/batches/{batch_id}/events")
async def batch_events(
    batch_id: str,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """SSE stream for batch status changes."""
    batch = db.query(Batch).filter(Batch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")

    queue = sse_manager.subscribe(batch_id)

    async def event_generator():
        try:
            while True:
                data = await queue.get()
                yield data
                # Stop yielding after a terminal validation event
                payload = json.loads(data.split("data: ")[1].split("\n")[0])
                if payload.get("status") in ("validated", "failed"):
                    break
        except asyncio.CancelledError:
            pass
        finally:
            sse_manager.unsubscribe(batch_id, queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/batches/{batch_id}")
def get_batch(
    batch_id: str,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    batch = db.query(Batch).filter(Batch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")

    if user.platform_role == "user" and batch.user_id != user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    return BatchOut.from_batch(batch)


@router.get("/batches")
def list_batches(
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(Batch)

    if user.platform_role == "user":
        query = query.filter(Batch.user_id == user.id)

    batches = query.order_by(Batch.created_at.desc()).all()

    return {
        "object": "list",
        "data": [BatchOut.from_batch(b) for b in batches],
    }
