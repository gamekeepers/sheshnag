"""POST /workers/report-failure with a journalled count (#167).

A daemon that crashes mid-batch hands the batch back on restart and reports
how many prompts it journalled. A hand-back that made progress since the last
one must not spend one of MAX_BATCH_ATTEMPTS. Otherwise a crash loop (an OOM
on a large batch) burns all three attempts in minutes and fails a batch whose
journal was nearly complete.
"""

from models import Batch, BatchAssignment, Worker, WorkerRuntime, RuntimeModel, unix_now
from sweeper import MAX_BATCH_ATTEMPTS
from tests.test_worker_poll import _register, _reset, _worker_key


def _assigned_batch(db, worker_id, batch_id="batch-resume", total=1000, attempts=0):
    db.add(Batch(
        id=batch_id, endpoint="/v1/chat/completions", input_file_id="f-resume",
        status="in_progress", model="poll-model", created_at=1,
        request_counts_total=total, attempts=attempts,
    ))
    db.add(BatchAssignment(
        batch_id=batch_id, worker_id=worker_id, org_id="org-x",
        worker_hostname="poll-box", assigned_at=unix_now(),
    ))
    db.commit()


def _report(auth_client, key, worker_id, journalled=None, batch_id="batch-resume"):
    body = {"job_id": batch_id, "worker_id": worker_id, "error": "worker restarted"}
    if journalled is not None:
        body["journalled"] = journalled
    return auth_client.post(
        "/workers/report-failure", json=body,
        headers={"Authorization": f"Bearer {key}"},
    )


def _reassign(db, worker_id, batch_id="batch-resume"):
    """What /workers/poll does when the requeued batch comes back here."""
    batch = db.query(Batch).filter_by(id=batch_id).one()
    batch.status = "in_progress"
    db.add(BatchAssignment(
        batch_id=batch_id, worker_id=worker_id, org_id="org-x",
        worker_hostname="poll-box", assigned_at=unix_now(),
    ))
    db.commit()


def _setup(auth_client, db, **kw):
    _reset(db)
    key = _worker_key(auth_client)
    worker_id = _register(auth_client, key)
    _assigned_batch(db, worker_id, **kw)
    return key, worker_id


def test_crash_loop_making_progress_never_fails_the_batch(auth_client, db_session):
    key, worker_id = _setup(auth_client, db_session)

    for journalled in (300, 600, 900, 950):        # more hand-backs than the cap
        resp = _report(auth_client, key, worker_id, journalled=journalled)
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "validated"
        assert resp.json()["attempts"] == 0
        db_session.expire_all()
        _reassign(db_session, worker_id)

    batch = db_session.query(Batch).filter_by(id="batch-resume").one()
    assert batch.resume_progress == 950


def test_hand_back_without_new_progress_spends_an_attempt(auth_client, db_session):
    key, worker_id = _setup(auth_client, db_session)

    assert _report(auth_client, key, worker_id, journalled=500).json()["attempts"] == 0
    for expected_attempts in range(1, MAX_BATCH_ATTEMPTS):
        db_session.expire_all()
        _reassign(db_session, worker_id)
        resp = _report(auth_client, key, worker_id, journalled=500)   # stuck
        assert resp.json()["status"] == "validated"
        assert resp.json()["attempts"] == expected_attempts

    db_session.expire_all()
    _reassign(db_session, worker_id)
    resp = _report(auth_client, key, worker_id, journalled=500)
    assert resp.json()["status"] == "failed"
    assert resp.json()["attempts"] == MAX_BATCH_ATTEMPTS


def test_ordinary_failure_report_still_spends_an_attempt(auth_client, db_session):
    key, worker_id = _setup(auth_client, db_session)
    resp = _report(auth_client, key, worker_id)
    assert resp.status_code == 200, resp.text
    assert resp.json()["attempts"] == 1


def test_journalled_count_is_capped_at_the_row_count(auth_client, db_session):
    """An inflated count cannot buy more free requeues than the batch has rows."""
    key, worker_id = _setup(auth_client, db_session, total=10)

    assert _report(auth_client, key, worker_id, journalled=10_000).json()["attempts"] == 0
    db_session.expire_all()
    _reassign(db_session, worker_id)
    assert _report(auth_client, key, worker_id, journalled=10_000).json()["attempts"] == 1


def test_negative_journalled_count_is_rejected(auth_client, db_session):
    key, worker_id = _setup(auth_client, db_session)
    assert _report(auth_client, key, worker_id, journalled=-1).status_code == 422
