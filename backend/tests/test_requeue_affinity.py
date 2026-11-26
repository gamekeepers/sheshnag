"""Requeue affinity (#166).

A hand-back that journalled more rows than any before it leaves the batch's
journal on that worker, and only a rerun there resumes it. So that worker
ranks the batch first, and other workers leave it alone for a short hold —
but only while that worker can actually take it. A batch is never left
waiting for a machine that is gone, busy or ineligible, and a worker that
keeps failing the batch does not keep getting it back.
"""

from datetime import datetime, timezone

from models import Batch, BatchAssignment, Worker, unix_now
from scheduler import JOURNAL_TTL_SECONDS
from sweeper import sweep_stale_workers
from tests.test_capability_gate import _register
from tests.test_worker_poll import _catalog, _poll, _reset, _worker_key


def _assign(db, worker_id, batch_id="batch-held", created_at=2):
    """A 1000-row batch in progress on `worker_id` (as /workers/poll leaves it)."""
    batch = db.get(Batch, batch_id)
    if batch is None:
        batch = Batch(id=batch_id, endpoint="/v1/chat/completions", input_file_id="f-aff",
                      model="poll-model", created_at=created_at, request_counts_total=1000)
        db.add(batch)
    batch.status = "in_progress"
    db.add(BatchAssignment(batch_id=batch_id, worker_id=worker_id, org_id="org-x",
                           worker_hostname="h", assigned_at=unix_now()))
    db.commit()


def _queue(db, batch_id, created_at):
    db.add(Batch(id=batch_id, endpoint="/v1/chat/completions", input_file_id="f-aff",
                 status="validated", model="poll-model", created_at=created_at,
                 request_counts_total=10))
    db.commit()


def _hand_back(auth_client, key, worker_id, journalled, shutdown=False, batch_id="batch-held"):
    resp = auth_client.post("/workers/report-failure", headers={"Authorization": f"Bearer {key}"}, json={
        "job_id": batch_id, "worker_id": worker_id, "error": "handed back",
        "journalled": journalled, "shutdown": shutdown,
    })
    assert resp.status_code == 200, resp.text
    return resp.json()


def _job(auth_client, key, worker_id):
    job = _poll(auth_client, key, worker_id).json()["job"]
    return job and job["job_id"]


def _fresh(db, batch_id="batch-held"):
    db.expire_all()
    return db.get(Batch, batch_id)


def _two_workers(auth_client, db):
    """`holder` journalled 900 rows of batch-held and handed it back."""
    _reset(db)
    key = _worker_key(auth_client)
    holder = _register(auth_client, key, hostname="holder", capabilities={})
    other = _register(auth_client, key, hostname="other", capabilities={})
    _catalog(db)
    _assign(db, holder)
    return key, holder, other


# ─── Rank ───────────────────────────────────────────────────

def test_the_worker_holding_the_journal_gets_the_batch_back(auth_client, db_session):
    key, holder, _ = _two_workers(auth_client, db_session)
    assert _hand_back(auth_client, key, holder, journalled=900)["attempts"] == 0
    assert _fresh(db_session).resume_worker_id == holder
    _queue(db_session, "batch-older", created_at=1)      # FIFO alone would pick this
    assert _job(auth_client, key, holder) == "batch-held"
    _reset(db_session)


def test_a_swept_journal_no_longer_ranks(auth_client, db_session):
    """The daemon deletes a journal untouched for a day: past that, the
    holder would start from row one, so the batch is not preferred there."""
    key, holder, _ = _two_workers(auth_client, db_session)
    _hand_back(auth_client, key, holder, journalled=900)
    _fresh(db_session).resume_reported_at = unix_now() - JOURNAL_TTL_SECONDS - 1
    db_session.commit()
    _queue(db_session, "batch-older", created_at=1)
    assert _job(auth_client, key, holder) == "batch-older"
    _reset(db_session)


# ─── Hold: only while the holder can take it ─────────────────

def test_another_worker_leaves_it_while_the_holder_is_about_to_poll(auth_client, db_session):
    key, holder, other = _two_workers(auth_client, db_session)
    _hand_back(auth_client, key, holder, journalled=900)
    assert _job(auth_client, key, other) is None

    # The hold is short: a holder that never polls cannot strand the batch.
    _fresh(db_session).resume_hold_until = unix_now() - 1
    db_session.commit()
    assert _job(auth_client, key, other) == "batch-held"
    _reset(db_session)


def test_a_gone_holder_holds_nothing(auth_client, db_session):
    """DoD: when that worker is gone, the batch goes elsewhere without delay."""
    key, holder, other = _two_workers(auth_client, db_session)
    _hand_back(auth_client, key, holder, journalled=900)
    db_session.get(Worker, holder).last_heartbeat = unix_now() - 3600
    db_session.commit()
    assert _job(auth_client, key, other) == "batch-held"
    _reset(db_session)


def test_a_busy_holder_holds_nothing(auth_client, db_session):
    """A daemon runs one batch at a time: busy, it will not poll until done."""
    key, holder, other = _two_workers(auth_client, db_session)
    _hand_back(auth_client, key, holder, journalled=900)
    _assign(db_session, holder, batch_id="batch-busy")
    assert _job(auth_client, key, other) == "batch-held"
    _reset(db_session)


def test_a_holder_with_finished_batches_still_holds(auth_client, db_session):
    """Assignments outlive their batch as history; only an in-progress one
    makes a holder busy."""
    key, holder, other = _two_workers(auth_client, db_session)
    _hand_back(auth_client, key, holder, journalled=900)
    _assign(db_session, holder, batch_id="batch-done")
    db_session.get(Batch, "batch-done").status = "completed"
    db_session.commit()
    assert _job(auth_client, key, other) is None
    _reset(db_session)


def test_an_off_hours_holder_holds_nothing(auth_client, db_session):
    key, holder, other = _two_workers(auth_client, db_session)
    _hand_back(auth_client, key, holder, journalled=900)
    now = datetime.now(timezone.utc)
    start = min((now.hour * 60 + now.minute + 120) % 1440, 1438)   # a minute, two hours away
    w = db_session.get(Worker, holder)
    w.window_start_min, w.window_end_min, w.window_tz = start, start + 1, "UTC"
    db_session.commit()
    assert _job(auth_client, key, other) == "batch-held"
    _reset(db_session)


def test_a_draining_holder_holds_nothing(auth_client, db_session):
    key, holder, other = _two_workers(auth_client, db_session)
    _hand_back(auth_client, key, holder, journalled=900)
    db_session.get(Worker, holder).status = "draining"
    db_session.commit()
    assert _job(auth_client, key, other) == "batch-held"
    _reset(db_session)


def test_a_holder_no_longer_eligible_holds_nothing(auth_client, db_session):
    """Its provider capped batch size below this one since the hand-back."""
    key, holder, other = _two_workers(auth_client, db_session)
    _hand_back(auth_client, key, holder, journalled=900)
    db_session.get(Worker, holder).max_batch_prompts = 10
    db_session.commit()
    assert _job(auth_client, key, other) == "batch-held"
    _reset(db_session)


def test_a_graceful_stop_leaves_no_hold_but_keeps_the_rank(auth_client, db_session):
    """The provider is taking the machine back: do not wait for it. If it
    returns before anyone else claims the batch, it still resumes it."""
    key, holder, other = _two_workers(auth_client, db_session)
    _hand_back(auth_client, key, holder, journalled=900, shutdown=True)
    batch = _fresh(db_session)
    assert batch.resume_worker_id == holder and batch.resume_hold_until is None
    _queue(db_session, "batch-older", created_at=1)
    assert _job(auth_client, key, holder) == "batch-held"
    _reset(db_session)


def test_without_a_hold_another_worker_takes_it_at_once(auth_client, db_session):
    key, holder, other = _two_workers(auth_client, db_session)
    _hand_back(auth_client, key, holder, journalled=900, shutdown=True)
    assert _job(auth_client, key, other) == "batch-held"
    _reset(db_session)


# ─── Affinity and the attempts cap ──────────────────────────

def test_a_failure_without_progress_on_the_holder_ends_affinity(auth_client, db_session):
    """Otherwise a worker that keeps failing the batch keeps getting it back."""
    key, holder, other = _two_workers(auth_client, db_session)
    _hand_back(auth_client, key, holder, journalled=900)
    _assign(db_session, holder)
    assert _hand_back(auth_client, key, holder, journalled=900)["attempts"] == 1   # stuck
    batch = _fresh(db_session)
    assert batch.resume_worker_id is None and batch.resume_hold_until is None
    assert _job(auth_client, key, other) == "batch-held"
    _reset(db_session)


def test_a_failure_on_another_worker_keeps_affinity(auth_client, db_session):
    """The journal is still on the holder; the other worker's failure says nothing about it."""
    key, holder, other = _two_workers(auth_client, db_session)
    _hand_back(auth_client, key, holder, journalled=900)
    _assign(db_session, other)
    assert _hand_back(auth_client, key, other, journalled=50)["attempts"] == 1
    assert _fresh(db_session).resume_worker_id == holder
    _reset(db_session)


def test_progress_on_another_worker_moves_affinity_there(auth_client, db_session):
    key, holder, other = _two_workers(auth_client, db_session)
    _hand_back(auth_client, key, holder, journalled=300)
    _assign(db_session, other)
    _hand_back(auth_client, key, other, journalled=600)
    assert _fresh(db_session).resume_worker_id == other
    _reset(db_session)


def test_the_sweeper_reclaiming_from_the_holder_ends_affinity(auth_client, db_session):
    key, holder, other = _two_workers(auth_client, db_session)
    _hand_back(auth_client, key, holder, journalled=900)
    _assign(db_session, holder)
    db_session.get(Worker, holder).last_heartbeat = unix_now() - 3600
    db_session.commit()
    sweep_stale_workers(db_session)
    batch = _fresh(db_session)
    assert batch.attempts == 1 and batch.resume_worker_id is None
    _reset(db_session)
