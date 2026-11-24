"""
Fault tolerance — requeue in-flight batches/finetune jobs and reclaim dead workers.

Two entry points:
    requeue_or_fail_batch() — shared by /workers/report-failure and the sweeper:
        requeues a batch (status back to "validated") until MAX_BATCH_ATTEMPTS,
        then marks it terminally failed.
    run_sweeper() — background loop started from main.py: workers whose
        heartbeats stopped are marked offline and their in-flight batches
        requeued, so a crashed daemon never strands a batch in "in_progress".

The worker sweep leaves finetune jobs alone: requeueing a six-hour training
run on a missed heartbeat restarts it from step 0 without the user knowing.
fail_lost_finetune_jobs() fails a running job whose runner has been silent
for FINETUNE_RUNNER_LOST_SECONDS, with a reason the user can see, and the
user resubmits. A failure the runner itself reports goes through
requeue_or_fail_finetune_job(), and only when the runner marks it retryable.
A resume point is a kind='checkpoint' row in finetune_artifacts.
"""
import asyncio
import json
import logging

from models import (
    Batch, BatchAssignment, FinetuneEvent, FinetuneJob, FinetuneJobAssignment, Worker, unix_now,
)
from services.finetune_events import transition

logger = logging.getLogger(__name__)

# Terminal failure after this many execution attempts (spec §12).
MAX_BATCH_ATTEMPTS = 3

# Separate constant from MAX_BATCH_ATTEMPTS even though it's the same value
# today — training runs are far more expensive to retry than an inference
# batch, so this is likely to get tuned down independently later.
MAX_FINETUNE_ATTEMPTS = 3

# forge-runner heartbeats every 30s, including while it supervises a run.
# Ten minutes of silence = the runner is gone. A false positive costs a whole
# training run, so the window is long.
FINETUNE_RUNNER_LOST_SECONDS = 600

# Daemon heartbeats every 30s — 4 missed beats ⇒ presumed dead.
HEARTBEAT_TIMEOUT_SECONDS = 120

SWEEP_INTERVAL_SECONDS = 60

# Usage-ingestion retries handled per sweep — each one parses a whole output
# file, so a backlog drains over several sweeps instead of in one.
MAX_INGESTIONS_PER_SWEEP = 5


def requeue_or_fail_batch(
    db, batch, error: str | None = None, count_attempt: bool = True,
    worker_id: str | None = None,
) -> str:
    """Requeue an in-flight batch, or fail it after MAX_BATCH_ATTEMPTS.

    Increments attempts (unless ``count_attempt`` is False, for a hand-back
    that made progress), voids the assignment, and returns the new status
    ("validated" or "failed"). Caller commits.

    ``worker_id`` is the worker the batch is being taken from. An attempt
    counted against the batch's affinity worker ends the affinity (#166):
    that worker holds the best journal, but it just failed the batch without
    adding to it, and sending the batch straight back would turn affinity
    into a retry loop on one machine. The journal stays on its disk; the
    batch simply competes for any worker again.
    """
    if count_attempt:
        batch.attempts = (batch.attempts or 0) + 1
        if worker_id is not None and batch.resume_worker_id == worker_id:
            batch.resume_worker_id = None
            batch.resume_hold_until = None
    db.query(BatchAssignment).filter(
        BatchAssignment.batch_id == batch.id,
    ).delete()
    if error:
        batch.error_details = error[:2000]

    if batch.attempts >= MAX_BATCH_ATTEMPTS:
        batch.status = "failed"
        batch.completed_at = unix_now()
        batch.request_counts_failed = batch.request_counts_total
        # Zero this too, or the pair double-counts: /workers/progress keeps the
        # *peak* completed count ever reported, so a batch that reached 800/1000
        # before dying would end as completed=800, failed=1000 — 1800 rows
        # accounted for out of 1000.
        batch.request_counts_completed = 0
        return "failed"

    batch.status = "validated"
    # A fresh attempt starts from zero. /workers/progress only moves these
    # counters forward (they arrive out of order from a pool of workers), so
    # leaving the previous attempt's high-water mark here would pin the batch
    # at, say, 800/1000 for the whole re-run until upload corrects it.
    batch.request_counts_completed = 0
    batch.request_counts_failed = 0
    return "validated"


def requeue_or_fail_finetune_job(db, job, error: str | None = None,
                                 code: str | None = None, retryable: bool = True) -> str:
    """Requeue a running finetune job, or fail it after MAX_FINETUNE_ATTEMPTS.

    Mirrors requeue_or_fail_batch, but goes through
    services.finetune_events.transition so the job's event log records
    each requeue. Training restarts from step 0 (see module docstring).
    """
    job.attempts = (job.attempts or 0) + 1
    db.query(FinetuneJobAssignment).filter(
        FinetuneJobAssignment.job_id == job.id,
    ).delete()
    reason = (error or "unknown error")[:2000]

    if not retryable or job.attempts >= MAX_FINETUNE_ATTEMPTS:
        job.error = json.dumps({"code": code or "training_failed", "param": None, "message": reason})
        transition(db, job, "failed",
                   f"Attempt {job.attempts}/{MAX_FINETUNE_ATTEMPTS} failed: {reason}", level="error")
        return "failed"

    transition(db, job, "queued",
               f"Attempt {job.attempts}/{MAX_FINETUNE_ATTEMPTS} failed, requeued: {reason}", level="warn")
    return "queued"


def sweep_stale_workers(db) -> tuple[int, int]:
    """Mark heartbeat-silent workers offline and requeue their batches.
    Finetune jobs are left alone here — see fail_lost_finetune_jobs().

    Returns (workers_marked_offline, batches_requeued).
    """
    cutoff = unix_now() - HEARTBEAT_TIMEOUT_SECONDS
    stale_workers = db.query(Worker).filter(
        Worker.status.in_(("online", "draining")),
        Worker.last_heartbeat < cutoff,
    ).all()

    requeued = 0
    for worker in stale_workers:
        worker.status = "offline"

        assignments = db.query(BatchAssignment).filter(
            BatchAssignment.worker_id == worker.id,
        ).all()
        for assignment in assignments:
            batch = db.query(Batch).filter(
                Batch.id == assignment.batch_id,
            ).first()
            if batch and batch.status == "in_progress":
                outcome = requeue_or_fail_batch(
                    db, batch,
                    error=f"Worker {worker.id} went offline mid-job",
                    worker_id=worker.id,
                )
                requeued += 1
                logger.warning(
                    "Reclaimed batch %s from offline worker %s → %s "
                    "(attempt %d/%d)",
                    batch.id, worker.id, outcome,
                    batch.attempts, MAX_BATCH_ATTEMPTS,
                )

        logger.warning(
            "Worker %s (%s) marked offline — no heartbeat since %s",
            worker.id, worker.hostname, worker.last_heartbeat,
        )

    if stale_workers:
        db.commit()
    return len(stale_workers), requeued


def sweep_pending_ingestions(db, limit: int = MAX_INGESTIONS_PER_SWEEP) -> int:
    """Retry usage ingestion for completed batches whose ingest never finished.

    Covers a background task that died mid-flight. At most ``limit`` batches
    per call, oldest first, so a backlog drains over several sweeps. Batches
    whose ingestion has raised MAX_USAGE_INGESTION_ATTEMPTS times are left
    alone. Blocking (each retry parses a whole JSONL file), so run_sweeper calls
    this off the event loop.
    """
    from sqlalchemy import func
    from services import usage_ingest
    from models import File as FileModel

    pending_batches = db.query(Batch).filter(
        Batch.status == "completed",
        Batch.usage_ingestion_pending.is_(True),
        func.coalesce(Batch.usage_ingestion_attempts, 0)
        < usage_ingest.MAX_USAGE_INGESTION_ATTEMPTS,
    ).order_by(Batch.completed_at).limit(limit).all()

    retried = 0
    for batch in pending_batches:
        output_file = db.query(FileModel).filter(FileModel.id == batch.output_file_id).first()
        if output_file is None:
            # Nothing to read, ever. Clear the flag so this row is not
            # re-queried every sweep.
            batch.usage_ingestion_pending = False
            db.commit()
            continue
        logger.info("Sweeper retrying usage ingestion for batch %s", batch.id)
        usage_ingest.ingest_usage_records(batch.id, output_file.filepath)
        retried += 1

    return retried


def _sweep_pending_ingestions_once() -> int:
    """Run one ingestion sweep in its own session (for asyncio.to_thread)."""
    from database import SessionLocal

    db = SessionLocal()
    try:
        return sweep_pending_ingestions(db)
    finally:
        db.close()
def _fail_finetune_job(db, job, code: str, message: str) -> None:
    job.error = json.dumps({"code": code, "param": None, "message": message})
    db.query(FinetuneJobAssignment).filter(FinetuneJobAssignment.job_id == job.id).delete()
    transition(db, job, "failed", message, level="error")


def fail_lost_finetune_jobs(db) -> int:
    """Fail running finetune jobs whose runner stopped heartbeating.

    A requeue would restart the run from step 0 without the user knowing, so
    the job fails with a reason the user can read. The message names no
    worker: users see an opaque job, never a provider's machine. Returns the
    number of jobs failed.
    """
    cutoff = unix_now() - FINETUNE_RUNNER_LOST_SECONDS
    lost = (
        db.query(FinetuneJob, Worker)
        .join(FinetuneJobAssignment, FinetuneJobAssignment.job_id == FinetuneJob.id)
        .join(Worker, Worker.id == FinetuneJobAssignment.worker_id)
        .filter(FinetuneJob.status == "running")
        .filter((Worker.last_heartbeat < cutoff) | (Worker.last_heartbeat.is_(None)))
        .all()
    )
    for job, worker in lost:
        minutes = FINETUNE_RUNNER_LOST_SECONDS // 60
        _fail_finetune_job(db, job, "runner_lost", (
            f"The training runner stopped responding for {minutes} minutes and the "
            "training run was lost. Resubmit the job to train again."))
        logger.warning("Failed finetune job %s: runner on worker %s lost", job.id, worker.id)
    if lost:
        db.commit()
    return len(lost)


def release_finetune_jobs_held_by(db, worker_id: str) -> int:
    """Settle the finetune jobs still assigned to a runner that asks for work.

    forge-runner polls only between jobs, so a poll from a worker that still
    holds a running job means the runner restarted and that run is gone. The
    runner keeps its worker id across restarts (registration matches on
    hostname) and heartbeats again at once, so fail_lost_finetune_jobs never
    sees it.

    - No training step logged yet: nothing was trained, and the job is
      requeued through requeue_or_fail_finetune_job, spending an attempt.
    - A step logged: the run is lost and the job fails with
      `runner_restarted`.
    - Not running (cancelled or finished while assigned): the assignment
      is dropped.

    Returns the number of assignments settled. Does not commit.
    """
    held = (
        db.query(FinetuneJob)
        .join(FinetuneJobAssignment, FinetuneJobAssignment.job_id == FinetuneJob.id)
        .filter(FinetuneJobAssignment.worker_id == worker_id)
        .all()
    )
    for job in held:
        if job.status != "running":
            db.query(FinetuneJobAssignment).filter(
                FinetuneJobAssignment.job_id == job.id).delete()
            continue
        trained = db.query(FinetuneEvent.id).filter(
            FinetuneEvent.job_id == job.id, FinetuneEvent.type == "metrics",
        ).first() is not None
        if trained:
            _fail_finetune_job(db, job, "runner_restarted", (
                "The training runner restarted during the run and the training "
                "progress was lost. Resubmit the job to train again."))
        else:
            requeue_or_fail_finetune_job(
                db, job, error="The training runner restarted before training began",
                code="runner_restarted")
        logger.warning("Released finetune job %s held by restarted runner %s (%s)",
                       job.id, worker_id, "failed" if trained else "requeued")
    return len(held)


async def run_sweeper() -> None:
    """Periodic sweep loop — started as an asyncio task at app startup."""
    from database import SessionLocal

    while True:
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
        db = SessionLocal()
        try:
            sweep_stale_workers(db)
            fail_lost_finetune_jobs(db)
        except Exception:
            logger.exception("Worker sweep failed — retrying next interval")
        finally:
            db.close()

        # Usage-ingestion retries read and parse whole output files — kept off
        # the event loop for the same reason as auto-adopt below.
        try:
            await asyncio.to_thread(_sweep_pending_ingestions_once)
        except Exception:
            logger.exception("Usage-ingestion sweep failed — retrying next interval")

        # Auto-adopt (#116): quarantined worker hashes the identity resolver
        # confirms against their public registry become catalogue entries.
        # Registry lookups are blocking HTTP with 20s timeouts — kept off
        # the event loop so heartbeats/polls never wait on them.
        try:
            from catalog_service import run_auto_adopt_once
            adopted = await asyncio.to_thread(run_auto_adopt_once)
            if adopted:
                logger.info("Auto-adopt pass registered %d model(s)", adopted)
        except Exception:
            logger.exception("Auto-adopt pass failed — retrying next interval")
