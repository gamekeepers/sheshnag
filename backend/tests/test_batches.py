"""Batch creation API tests.

Locks in the OpenAI-compatible request shape for POST /v1/batches:
the request carries NO top-level `model` field — the model is derived
from `body.model` in the JSONL file by the validator. A stray `model`
key (e.g. from an older dashboard build) must be ignored, not rejected.
"""

import pytest
from sqlalchemy.orm import sessionmaker

from models import File as FileModel


@pytest.fixture
def seeded_file(_engine, _test_user, tmp_path):
    """A valid single-line JSONL input file owned by the test user."""
    jsonl = tmp_path / "input.jsonl"
    jsonl.write_text(
        '{"custom_id": "req-1", "method": "POST", "url": "/v1/chat/completions", '
        '"body": {"model": "llama3:8b", "messages": [{"role": "user", "content": "hi"}]}}\n'
    )

    SM = sessionmaker(bind=_engine, expire_on_commit=False)
    db = SM()
    try:
        record = FileModel(
            user_id=_test_user.id,
            filename="input.jsonl",
            purpose="batch",
            bytes=jsonl.stat().st_size,
            filepath=str(jsonl),
        )
        db.add(record)
        db.commit()
        db.refresh(record)
        return record
    finally:
        db.close()


def _batch_payload(file_id, **extra):
    payload = {
        "input_file_id": file_id,
        "endpoint": "/v1/chat/completions",
        "completion_window": "24h",
    }
    payload.update(extra)
    return payload


def test_create_batch_without_model_field(auth_client, seeded_file):
    """OpenAI shape: no top-level model — must be accepted."""
    res = auth_client.post("/v1/batches", json=_batch_payload(seeded_file.id))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "validating"
    assert body["input_file_id"] == seeded_file.id


def test_create_batch_ignores_extra_model_field(auth_client, seeded_file):
    """A stray top-level `model` (older clients) is tolerated, not a 422."""
    res = auth_client.post(
        "/v1/batches",
        json=_batch_payload(seeded_file.id, model="llama3:8b"),
    )
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "validating"


def test_create_batch_unknown_file_rejected(auth_client):
    res = auth_client.post("/v1/batches", json=_batch_payload("file-does-not-exist"))
    assert res.status_code == 400


# ── in_progress_at ──────────────────────────────────────────────────────────
# The dashboard's lifecycle timeline needs the moment a worker took the job.
# That is on BatchAssignment.assigned_at, not on Batch, so BatchOut has to
# reach across for it — and a batch that is queued must be distinguishable from
# one that is running, which is exactly the difference the field carries.

def _seed_batch(engine, user_id, file_id, status="validated"):
    from models import Batch

    SM = sessionmaker(bind=engine, expire_on_commit=False)
    db = SM()
    try:
        batch = Batch(
            user_id=user_id,
            endpoint="/v1/chat/completions",
            input_file_id=file_id,
            completion_window="24h",
            status=status,
        )
        db.add(batch)
        db.commit()
        db.refresh(batch)
        return batch
    finally:
        db.close()


def _assign(engine, batch_id, assigned_at):
    from models import BatchAssignment

    SM = sessionmaker(bind=engine, expire_on_commit=False)
    db = SM()
    try:
        db.add(BatchAssignment(
            batch_id=batch_id,
            worker_id="wrk_test",
            org_id="org_test",
            worker_hostname="test-host",
            assigned_at=assigned_at,
        ))
        db.commit()
    finally:
        db.close()


def test_queued_batch_reports_no_in_progress_at(auth_client, _engine, _test_user, seeded_file):
    """Never dispatched, so there is no start time to report — not a zero."""
    batch = _seed_batch(_engine, _test_user.id, seeded_file.id)

    body = auth_client.get(f"/v1/batches/{batch.id}").json()
    assert body["status"] == "validated"
    assert body["in_progress_at"] is None


def test_in_progress_at_comes_from_the_assignment(auth_client, _engine, _test_user, seeded_file):
    batch = _seed_batch(_engine, _test_user.id, seeded_file.id, status="in_progress")
    _assign(_engine, batch.id, 1_700_000_000)

    body = auth_client.get(f"/v1/batches/{batch.id}").json()
    assert body["in_progress_at"] == 1_700_000_000


def test_list_batches_carries_in_progress_at_per_row(auth_client, _engine, _test_user, seeded_file):
    """One query for the page, so the field must land on the right rows only."""
    queued = _seed_batch(_engine, _test_user.id, seeded_file.id)
    running = _seed_batch(_engine, _test_user.id, seeded_file.id, status="in_progress")
    _assign(_engine, running.id, 1_700_000_500)

    rows = {b["id"]: b for b in auth_client.get("/v1/batches").json()["data"]}
    assert rows[running.id]["in_progress_at"] == 1_700_000_500
    assert rows[queued.id]["in_progress_at"] is None
