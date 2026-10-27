"""
Finetune job events and status transitions.

Every status change goes through transition(), so each one is checked
against finetune_contract.VALID_TRANSITIONS, stamps the matching timestamp,
and leaves a line in finetune_events. Neither function commits — callers
own the transaction.
"""
import json

from finetune_contract import TERMINAL_STATUSES, can_transition
from models import FinetuneEvent, unix_now


class InvalidTransition(ValueError):
    pass


def emit_event(db, job_id: str, message: str, *, level: str = "info",
               type: str = "message", data: dict | None = None) -> FinetuneEvent:
    event = FinetuneEvent(
        job_id=job_id, level=level, type=type, message=message,
        data=json.dumps(data) if data is not None else None,
    )
    db.add(event)
    return event


def transition(db, job, target: str, message: str | None = None, *, level: str = "info") -> None:
    if not can_transition(job.status, target):
        raise InvalidTransition(f"Cannot move finetune job from '{job.status}' to '{target}'")

    now = unix_now()
    job.status = target
    if target == "queued":
        job.queued_at = now
        job.started_at = None  # a requeued job starts again
    elif target == "running":
        job.started_at = now
    if target in TERMINAL_STATUSES:
        job.finished_at = now

    emit_event(db, job.id, message or f"Job {target}", level=level,
               data={"status": target})
