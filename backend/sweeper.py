"""
Fault tolerance — requeue in-flight batches and reclaim dead workers.

Two entry points:
    requeue_or_fail_batch() — shared by /workers/report-failure and the sweeper:
        requeues a batch (status back to "validated") until MAX_BATCH_ATTEMPTS,
        then marks it terminally failed.
    run_sweeper() — background loop started from main.py: workers whose
        heartbeats stopped are marked offline and their in-flight batches
        requeued, so a crashed daemon never strands a batch in "in_progress".
"""
import asyncio
import logging

from models import Batch, BatchAssignment, Worker, unix_now

logger = logging.getLogger(__name__)

# Terminal failure after this many execution attempts (spec §12).
MAX_BATCH_ATTEMPTS = 3

# Daemon heartbeats every 30s — 4 missed beats ⇒ presumed dead.
HEARTBEAT_TIMEOUT_SECONDS = 120

SWEEP_INTERVAL_SECONDS = 60


def requeue_or_fail_batch(db, batch, error: str | None = None) -> str:
    """Requeue an in-flight batch, or fail it after MAX_BATCH_ATTEMPTS.

    Increments attempts, voids the assignment, and returns the new status
    ("validated" or "failed"). Caller commits.
    """
    batch.attempts = (batch.attempts or 0) + 1
    db.query(BatchAssignment).filter(
        BatchAssignment.batch_id == batch.id,
    ).delete()
    if error:
        batch.error_details = error[:2000]

    if batch.attempts >= MAX_BATCH_ATTEMPTS:
        batch.status = "failed"
        batch.completed_at = unix_now()
        batch.request_counts_failed = batch.request_counts_total
        return "failed"

    batch.status = "validated"
    return "validated"


def sweep_stale_workers(db) -> tuple[int, int]:
    """Mark heartbeat-silent workers offline and requeue their batches.

    Returns (workers_marked_offline, batches_requeued).
    """
    cutoff = unix_now() - HEARTBEAT_TIMEOUT_SECONDS
    stale_workers = db.query(Worker).filter(
        Worker.status == "online",
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


async def run_sweeper() -> None:
    """Periodic sweep loop — started as an asyncio task at app startup."""
    from database import SessionLocal

    while True:
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
        db = SessionLocal()
        try:
            sweep_stale_workers(db)
        except Exception:
            logger.exception("Worker sweep failed — retrying next interval")
        finally:
            db.close()
