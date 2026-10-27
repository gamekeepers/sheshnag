import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from database import get_db
from models import FinetuneJob, File as FileModel
from schemas import FinetuneJobCreate, FinetuneJobOut
from auth import get_human_context
from services.finetune_validator import validate_finetune_dataset
from services.sse_manager import sse_manager
from finetune_picker import is_base_model_supported

router = APIRouter()


async def _validate_and_notify(job_id: str, filepath: str) -> None:
    """Run dataset validation in a background thread, then push an SSE event.
    Mirrors batches.py's _validate_and_notify exactly — same sse_manager,
    keyed generically by job id."""
    result = await asyncio.to_thread(validate_finetune_dataset, job_id, filepath)
    status = "validated" if result else "failed"
    await sse_manager.publish(
        job_id,
        "validation_complete",
        {"job_id": job_id, "status": status},
    )


@router.post("/finetune-jobs")
async def create_finetune_job(
    req: FinetuneJobCreate,
    ctx=Depends(get_human_context),
    db: Session = Depends(get_db),
):
    user, api_key = ctx

    dataset_file = db.query(FileModel).filter(FileModel.id == req.dataset_file_id).first()
    if not dataset_file:
        raise HTTPException(status_code=400, detail=f"Dataset file '{req.dataset_file_id}' not found")

    if user.platform_role == "user" and dataset_file.user_id != user.id:
        raise HTTPException(status_code=403, detail="You don't own this file")

    if not is_base_model_supported(db, req.base_model_id):
        raise HTTPException(
            status_code=400,
            detail=f"Model '{req.base_model_id}' is not a finetune-eligible catalogue entry. "
                    "Choose a huggingface-sourced id from GET /v1/models.",
        )

    job = FinetuneJob(
        user_id=user.id,
        api_key_id=api_key.id if api_key else None,
        dataset_file_id=req.dataset_file_id,
        base_model_id=req.base_model_id,
        lora_rank=req.lora_rank,
        lora_alpha=req.lora_alpha,
        lora_dropout=req.lora_dropout,
        target_modules=req.target_modules,
        epochs=req.epochs,
        learning_rate=req.learning_rate,
        micro_batch_size=req.micro_batch_size,
        max_seq_length=req.max_seq_length,
        status="validating",
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    asyncio.create_task(_validate_and_notify(job.id, dataset_file.filepath))

    return FinetuneJobOut.from_job(job)


@router.get("/finetune-jobs/{job_id}/events")
async def finetune_job_events(
    job_id: str,
    ctx=Depends(get_human_context),
    db: Session = Depends(get_db),
):
    """SSE stream for finetune job status changes — dataset validation now,
    training progress once the daemon-side executor exists."""
    user, _api_key = ctx

    job = db.query(FinetuneJob).filter(FinetuneJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Finetune job not found")
    if user.platform_role == "user" and job.user_id != user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    queue = sse_manager.subscribe(job_id)

    async def event_generator():
        try:
            while True:
                data = await queue.get()
                yield data
                payload = json.loads(data.split("data: ")[1].split("\n")[0])
                if payload.get("status") in ("validated", "failed", "completed"):
                    break
        except asyncio.CancelledError:
            pass
        finally:
            sse_manager.unsubscribe(job_id, queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/finetune-jobs/{job_id}")
def get_finetune_job(
    job_id: str,
    ctx=Depends(get_human_context),
    db: Session = Depends(get_db),
):
    user, _api_key = ctx

    job = db.query(FinetuneJob).filter(FinetuneJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Finetune job not found")
    if user.platform_role == "user" and job.user_id != user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    return FinetuneJobOut.from_job(job)


@router.get("/finetune-jobs")
def list_finetune_jobs(
    ctx=Depends(get_human_context),
    db: Session = Depends(get_db),
):
    user, _api_key = ctx

    query = db.query(FinetuneJob)
    if user.platform_role == "user":
        query = query.filter(FinetuneJob.user_id == user.id)

    jobs = query.order_by(FinetuneJob.created_at.desc()).all()

    return {
        "object": "list",
        "data": [FinetuneJobOut.from_job(j) for j in jobs],
    }
