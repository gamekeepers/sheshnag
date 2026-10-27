"""
/v1/fine_tuning/* — Sheshnag's own finetuning API, shaped after OpenAI's.

Same credentials as /v1/files and /v1/batches (JWT or personal API key),
same /v1/files upload for datasets (purpose="fine-tune"). The contract —
statuses, limits, what serving needs — is finetune_contract.py; the prose
version is docs/finetune_contract.md.
"""
import json
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from auth import get_human_context
from database import get_db
from finetune_contract import ENGINE, ENGINE_VERSION, TERMINAL_STATUSES
from finetune_picker import get_base_model_entry
from models import FinetuneArtifact, FinetuneEvent, FinetuneJob, File as FileModel
from schemas import FinetuneJobCreate
from services.finetune_events import emit_event, transition
from services.finetune_validator import validate_finetune_job

router = APIRouter()

DATASET_PURPOSE = "fine-tune"


# ─── Serialisation ─────────────────────────────────────────

def _loads(value):
    return json.loads(value) if value else None


def job_out(db: Session, job: FinetuneJob) -> dict:
    targets = job.target_modules
    adapter_file_ids = [a.file_id for a in db.query(FinetuneArtifact).filter(
        FinetuneArtifact.job_id == job.id, FinetuneArtifact.kind == "adapter",
    ).order_by(FinetuneArtifact.created_at)]
    return {
        "id": job.id,
        "object": "fine_tuning.job",
        "model": job.base_model_id,
        "base_model": {"source_ref": job.base_source_ref, "revision": job.base_source_revision},
        "training_file": job.training_file_id,
        "validation_file": job.validation_file_id,
        "method": {"type": job.method},
        "hyperparameters": {
            "n_epochs": job.n_epochs,
            "batch_size": job.batch_size,
            "learning_rate": job.learning_rate,
            "max_seq_length": job.max_seq_length,
        },
        "lora": {
            "rank": job.lora_rank,
            "alpha": job.lora_alpha,
            "dropout": job.lora_dropout,
            "target_modules": targets if targets == "all-linear" else json.loads(targets),
        },
        "engine": {"name": ENGINE, "version": ENGINE_VERSION},
        "seed": job.seed,
        "suffix": job.suffix,
        "status": job.status,
        "error": _loads(job.error),
        "training_file_stats": _loads(job.training_file_stats),
        "trained_tokens": job.trained_tokens,
        "fine_tuned_model": job.fine_tuned_model,
        "result_files": adapter_file_ids,
        "created_at": job.created_at,
        "queued_at": job.queued_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
    }


def event_out(event: FinetuneEvent) -> dict:
    return {
        "id": f"ftevent-{event.id}",
        "object": "fine_tuning.job.event",
        "created_at": event.created_at,
        "level": event.level,
        "type": event.type,
        "message": event.message,
        "data": _loads(event.data),
    }


def artifact_out(a: FinetuneArtifact) -> dict:
    return {
        "id": a.id,
        "object": "fine_tuning.job.artifact",
        "job_id": a.job_id,
        "kind": a.kind,
        "file_id": a.file_id,
        "step": a.step,
        "sha256": a.sha256,
        "bytes": a.bytes,
        "base_model": {"id": a.base_model_id, "source_ref": a.base_source_ref,
                       "revision": a.base_source_revision},
        "peft_type": a.peft_type,
        "lora_rank": a.lora_rank,
        "lora_alpha": a.lora_alpha,
        "target_modules": _loads(a.target_modules),
        "modules_to_save": _loads(a.modules_to_save),
        "engine": {"name": a.engine, "version": a.engine_version, "image": a.engine_image},
        "metrics": _loads(a.metrics),
        "servable": a.servable,
        "unservable_reasons": _loads(a.unservable_reasons) or [],
        "catalog_entry_id": a.catalog_entry_id,
        "created_at": a.created_at,
    }


# ─── Access ────────────────────────────────────────────────

def _get_owned_job(db: Session, user, job_id: str) -> FinetuneJob:
    job = db.query(FinetuneJob).filter(FinetuneJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Fine-tuning job not found")
    if user.platform_role == "user" and job.user_id != user.id:
        raise HTTPException(status_code=403, detail="Access denied")
    return job


def _check_dataset_file(db: Session, user, file_id: str, param: str) -> FileModel:
    f = db.query(FileModel).filter(FileModel.id == file_id).first()
    if not f:
        raise HTTPException(status_code=400, detail=f"{param}: file '{file_id}' not found")
    if user.platform_role == "user" and f.user_id != user.id:
        raise HTTPException(status_code=403, detail=f"{param}: you don't own file '{file_id}'")
    if f.purpose != DATASET_PURPOSE:
        raise HTTPException(
            status_code=400,
            detail=f"{param}: file '{file_id}' was uploaded with purpose '{f.purpose}'. "
                   f"Upload it again with POST /v1/files and purpose='{DATASET_PURPOSE}'.",
        )
    return f


# ─── Routes ────────────────────────────────────────────────

@router.post("/fine_tuning/jobs")
def create_fine_tuning_job(
    req: FinetuneJobCreate,
    background_tasks: BackgroundTasks,
    ctx=Depends(get_human_context),
    db: Session = Depends(get_db),
):
    user, api_key = ctx

    entry = get_base_model_entry(db, req.model)
    if not entry:
        raise HTTPException(
            status_code=400,
            detail=f"model: '{req.model}' is not a finetune-eligible catalogue entry. "
                   "Use the id of a huggingface-sourced model from GET /v1/models.",
        )
    _check_dataset_file(db, user, req.training_file, "training_file")
    if req.validation_file:
        _check_dataset_file(db, user, req.validation_file, "validation_file")

    lora = req.lora
    job = FinetuneJob(
        user_id=user.id,
        api_key_id=api_key.id if api_key else None,
        base_model_id=entry.id,
        base_source_ref=entry.source_ref or entry.runtime_model_id,
        base_source_revision=entry.source_revision,
        training_file_id=req.training_file,
        validation_file_id=req.validation_file,
        method=req.method.type,
        suffix=req.suffix,
        seed=req.seed,
        n_epochs=req.hyperparameters.n_epochs,
        batch_size=req.hyperparameters.batch_size,
        learning_rate=req.hyperparameters.learning_rate,
        max_seq_length=req.hyperparameters.max_seq_length,
        lora_rank=lora.rank,
        lora_alpha=lora.alpha,
        lora_dropout=lora.dropout,
        target_modules=lora.target_modules if isinstance(lora.target_modules, str)
                       else json.dumps(lora.target_modules),
        status="validating_files",
    )
    db.add(job)
    db.flush()
    emit_event(db, job.id, f"Created fine-tuning job {job.id}", data={"status": "validating_files"})
    db.commit()
    db.refresh(job)

    background_tasks.add_task(validate_finetune_job, job.id)
    return job_out(db, job)


@router.get("/fine_tuning/jobs")
def list_fine_tuning_jobs(
    after: Optional[str] = None,
    limit: int = Query(20, ge=1, le=100),
    ctx=Depends(get_human_context),
    db: Session = Depends(get_db),
):
    """Newest first; `after` is the last job id of the previous page."""
    user, _api_key = ctx

    query = db.query(FinetuneJob)
    if user.platform_role == "user":
        query = query.filter(FinetuneJob.user_id == user.id)
    if after:
        cursor = query.filter(FinetuneJob.id == after).first()
        if not cursor:
            raise HTTPException(status_code=400, detail=f"after: unknown job id '{after}'")
        query = query.filter(
            (FinetuneJob.created_at < cursor.created_at)
            | ((FinetuneJob.created_at == cursor.created_at) & (FinetuneJob.id < cursor.id))
        )

    jobs = query.order_by(FinetuneJob.created_at.desc(), FinetuneJob.id.desc()).limit(limit + 1).all()
    return {
        "object": "list",
        "data": [job_out(db, j) for j in jobs[:limit]],
        "has_more": len(jobs) > limit,
    }


@router.get("/fine_tuning/jobs/{job_id}")
def get_fine_tuning_job(
    job_id: str,
    ctx=Depends(get_human_context),
    db: Session = Depends(get_db),
):
    user, _api_key = ctx
    return job_out(db, _get_owned_job(db, user, job_id))


@router.post("/fine_tuning/jobs/{job_id}/cancel")
def cancel_fine_tuning_job(
    job_id: str,
    ctx=Depends(get_human_context),
    db: Session = Depends(get_db),
):
    """A running job stops at its worker's next progress report, which is
    answered with {"stop": true}."""
    user, _api_key = ctx
    job = _get_owned_job(db, user, job_id)

    if job.status in TERMINAL_STATUSES:
        raise HTTPException(status_code=409, detail=f"Job is already {job.status}")
    transition(db, job, "cancelled", "Job cancelled by user")
    db.commit()
    db.refresh(job)
    return job_out(db, job)


@router.get("/fine_tuning/jobs/{job_id}/events")
def list_fine_tuning_events(
    job_id: str,
    after: Optional[str] = None,
    limit: int = Query(20, ge=1, le=1000),
    ctx=Depends(get_human_context),
    db: Session = Depends(get_db),
):
    """Newest first, like OpenAI's; `after` is the last event id of the
    previous page (`ftevent-<n>`)."""
    user, _api_key = ctx
    job = _get_owned_job(db, user, job_id)

    query = db.query(FinetuneEvent).filter(FinetuneEvent.job_id == job.id)
    if after:
        try:
            cursor = int(after.removeprefix("ftevent-"))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"after: '{after}' is not an event id")
        query = query.filter(FinetuneEvent.id < cursor)

    events = query.order_by(FinetuneEvent.id.desc()).limit(limit + 1).all()
    return {
        "object": "list",
        "data": [event_out(e) for e in events[:limit]],
        "has_more": len(events) > limit,
    }


@router.get("/fine_tuning/jobs/{job_id}/artifacts")
def list_fine_tuning_artifacts(
    job_id: str,
    ctx=Depends(get_human_context),
    db: Session = Depends(get_db),
):
    user, _api_key = ctx
    job = _get_owned_job(db, user, job_id)
    artifacts = db.query(FinetuneArtifact).filter(
        FinetuneArtifact.job_id == job.id,
    ).order_by(FinetuneArtifact.created_at).all()
    return {"object": "list", "data": [artifact_out(a) for a in artifacts]}
