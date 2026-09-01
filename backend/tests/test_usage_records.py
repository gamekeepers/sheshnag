import io
import json
import os
import tempfile
import pytest
from models import Batch, File, UsageRecord, Organization, OrganizationMembership, User
from services.usage_ingest import ingest_usage_records


def _create_test_batch(db, user, endpoint="/v1/chat/completions", model="llama-3-8b"):
    batch = Batch(
        user_id=user.id,
        endpoint=endpoint,
        model=model,
        input_file_id="file-test-input",
        status="in_progress",
    )
    db.add(batch)
    db.commit()
    db.refresh(batch)
    return batch


def test_ingest_usage_records_creates_rows_and_rolls_up(db_session, test_user, auth_client):
    batch = _create_test_batch(db_session, test_user)

    # Prepare output JSONL with 2 completed prompts
    lines = [
        {
            "custom_id": "req-1",
            "response": {
                "id": "chatcmpl-1",
                "object": "chat.completion",
                "usage": {
                    "prompt_tokens": 15,
                    "completion_tokens": 25,
                    "total_tokens": 40,
                },
            },
            "error": None,
        },
        {
            "custom_id": "req-2",
            "response": {
                "id": "chatcmpl-2",
                "object": "chat.completion",
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 20,
                    "total_tokens": 30,
                },
            },
            "error": None,
        },
    ]

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8") as tmp:
        for item in lines:
            tmp.write(json.dumps(item) + "\n")
        tmp_path = tmp.name

    try:
        ingest_usage_records(batch.id, tmp_path)

        # Check usage_records in DB
        records = (
            db_session.query(UsageRecord)
            .filter(UsageRecord.batch_id == batch.id)
            .order_by(UsageRecord.custom_id.asc())
            .all()
        )
        assert len(records) == 2
        assert records[0].custom_id == "req-1"
        assert records[0].prompt_tokens == 15
        assert records[0].completion_tokens == 25
        assert records[0].total_tokens == 40
        assert records[0].model == "llama-3-8b"

        assert records[1].custom_id == "req-2"
        assert records[1].prompt_tokens == 10
        assert records[1].completion_tokens == 20
        assert records[1].total_tokens == 30

        # Check rollup on Batch
        db_session.refresh(batch)
        assert batch.prompt_tokens == 25
        assert batch.completion_tokens == 45
        assert batch.total_tokens == 70

        # Verify GET /v1/batches/{id} includes usage block
        res = auth_client.get(f"/v1/batches/{batch.id}")
        assert res.status_code == 200
        data = res.json()
        assert data["usage"] == {
            "prompt_tokens": 25,
            "completion_tokens": 45,
            "total_tokens": 70,
        }
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def test_ingest_usage_records_is_idempotent_on_reupload(db_session, test_user):
    batch = _create_test_batch(db_session, test_user)

    lines = [
        {
            "custom_id": "prompt-a",
            "response": {
                "usage": {"prompt_tokens": 5, "completion_tokens": 10, "total_tokens": 15}
            },
            "error": None,
        }
    ]

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8") as tmp:
        for item in lines:
            tmp.write(json.dumps(item) + "\n")
        tmp_path = tmp.name

    try:
        # Run twice
        ingest_usage_records(batch.id, tmp_path)
        ingest_usage_records(batch.id, tmp_path)

        records = db_session.query(UsageRecord).filter(UsageRecord.batch_id == batch.id).all()
        assert len(records) == 1

        db_session.refresh(batch)
        assert batch.prompt_tokens == 5
        assert batch.completion_tokens == 10
        assert batch.total_tokens == 15
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def test_ingest_embeddings_batch_with_missing_completion_tokens(db_session, test_user):
    batch = _create_test_batch(db_session, test_user, endpoint="/v1/embeddings", model="nomic-embed-text")

    lines = [
        {
            "custom_id": "embed-1",
            "response": {
                "object": "list",
                "usage": {
                    "prompt_tokens": 12,
                    "total_tokens": 12,
                    # Note: no completion_tokens key
                },
            },
            "error": None,
        }
    ]

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8") as tmp:
        for item in lines:
            tmp.write(json.dumps(item) + "\n")
        tmp_path = tmp.name

    try:
        ingest_usage_records(batch.id, tmp_path)

        rec = db_session.query(UsageRecord).filter(UsageRecord.batch_id == batch.id).first()
        assert rec is not None
        assert rec.prompt_tokens == 12
        assert rec.completion_tokens == 0
        assert rec.total_tokens == 12

        db_session.refresh(batch)
        assert batch.prompt_tokens == 12
        assert batch.completion_tokens == 0
        assert batch.total_tokens == 12
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def test_batch_with_no_usage_stays_null(db_session, test_user, auth_client):
    batch = _create_test_batch(db_session, test_user)

    # Legacy output without usage dict
    lines = [
        {
            "custom_id": "legacy-1",
            "response": {"output": "text without usage"},
            "error": None,
        }
    ]

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8") as tmp:
        for item in lines:
            tmp.write(json.dumps(item) + "\n")
        tmp_path = tmp.name

    try:
        ingest_usage_records(batch.id, tmp_path)

        db_session.refresh(batch)
        assert batch.prompt_tokens is None
        assert batch.completion_tokens is None
        assert batch.total_tokens is None

        res = auth_client.get(f"/v1/batches/{batch.id}")
        assert res.status_code == 200
        assert res.json()["usage"] is None
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def test_get_batch_usage_endpoint(db_session, test_user, auth_client, superadmin_client):
    batch = _create_test_batch(db_session, test_user)

    # Create usage records directly
    rec1 = UsageRecord(
        batch_id=batch.id,
        custom_id="req-01",
        model="llama-3-8b",
        prompt_tokens=10,
        completion_tokens=20,
        total_tokens=30,
    )
    rec2 = UsageRecord(
        batch_id=batch.id,
        custom_id="req-02",
        model="llama-3-8b",
        prompt_tokens=15,
        completion_tokens=25,
        total_tokens=40,
    )
    db_session.add_all([rec1, rec2])
    db_session.commit()

    # Owner can get usage
    res = auth_client.get(f"/v1/batches/{batch.id}/usage")
    assert res.status_code == 200
    data = res.json()
    assert data["object"] == "list"
    assert len(data["data"]) == 2
    assert data["data"][0]["custom_id"] == "req-01"
    assert data["data"][0]["total_tokens"] == 30
    assert data["data"][1]["custom_id"] == "req-02"
    assert data["data"][1]["total_tokens"] == 40

    # Superadmin can also get usage
    res_admin = superadmin_client.get(f"/v1/batches/{batch.id}/usage")
    assert res_admin.status_code == 200
    assert len(res_admin.json()["data"]) == 2

    # Non-existent batch -> 404
    res_404 = auth_client.get("/v1/batches/non-existent-id/usage")
    assert res_404.status_code == 404


def _worker_fixture(db_session, test_user, key_val, org_name, hostname):
    """Worker + org + worker API key + an in_progress batch assigned to it."""
    from auth import hash_api_key
    from models import ApiKey, Worker, BatchAssignment

    org = Organization(name=org_name)
    db_session.add(org)
    db_session.flush()

    api_key = ApiKey(
        org_id=org.id,
        key_type="worker",
        created_by_user_id=test_user.id,
        name="Test Worker Key",
        key_prefix=key_val[:8],
        key_hash=hash_api_key(key_val),
        status="active",
    )
    db_session.add(api_key)
    db_session.flush()

    worker = Worker(org_id=org.id, hostname=hostname, status="online")
    db_session.add(worker)
    db_session.flush()

    batch = Batch(
        user_id=test_user.id,
        endpoint="/v1/chat/completions",
        model="llama3:8b",
        input_file_id="file-input-1",
        status="in_progress",
        request_counts_total=10,
    )
    db_session.add(batch)
    db_session.flush()

    db_session.add(BatchAssignment(
        batch_id=batch.id,
        worker_id=worker.id,
        org_id=org.id,
        worker_hostname=worker.hostname,
    ))
    db_session.commit()
    return org, worker, batch


def test_worker_progress_does_not_touch_token_rollups(db_session, test_user, _engine):
    """Progress reports move request counts only.

    The daemon has no sender for live token counts, and a late report would
    overwrite the authoritative totals that ingestion writes at upload time.
    Token fields on the payload must be ignored.
    """
    from fastapi.testclient import TestClient
    from main import app
    from database import get_db
    from tests.conftest import _make_override

    key_val = "gk-test-worker-key-12345678"
    org, worker, batch = _worker_fixture(
        db_session, test_user, key_val, "Worker Progress Org", "worker-progress-1"
    )

    app.dependency_overrides[get_db] = _make_override(_engine)
    try:
        with TestClient(app) as client:
            client.headers.update({"Authorization": f"Bearer {key_val}"})
            res = client.post(
                "/workers/progress",
                json={
                    "job_id": batch.id,
                    "worker_id": worker.id,
                    "completed": 5,
                    "failed": 0,
                    "total": 10,
                    # Ignored: no longer part of ProgressReport.
                    "prompt_tokens": 100,
                    "completion_tokens": 250,
                    "total_tokens": 350,
                },
            )
            assert res.status_code == 200
            assert res.json()["status"] == "ok"

            db_session.refresh(batch)
            assert batch.request_counts_completed == 5
            assert batch.prompt_tokens is None
            assert batch.completion_tokens is None
            assert batch.total_tokens is None
    finally:
        app.dependency_overrides.clear()


def test_upload_results_endpoint_ingests_usage(db_session, test_user, _engine):
    """End-to-end through POST /workers/upload-results.

    Regression guard for scheduling ingestion off a sync handler: the previous
    asyncio.create_task call raised RuntimeError (no running event loop) inside
    FastAPI's worker thread, so every real upload 500'd and usage was never
    recorded. Calling ingest_usage_records() directly cannot catch that.
    """
    from fastapi.testclient import TestClient
    from main import app
    from database import get_db
    from tests.conftest import _make_override

    key_val = "gk-test-upload-key-12345678"
    org, worker, batch = _worker_fixture(
        db_session, test_user, key_val, "Worker Upload Org", "worker-upload-1"
    )

    payload = "\n".join(json.dumps(o) for o in [
        {"custom_id": "req-1", "error": None, "response": {
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}}},
        {"custom_id": "req-2", "error": None, "response": {
            "usage": {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12}}},
    ])

    app.dependency_overrides[get_db] = _make_override(_engine)
    try:
        with TestClient(app) as client:
            client.headers.update({"Authorization": f"Bearer {key_val}"})
            res = client.post(
                "/workers/upload-results",
                data={"job_id": batch.id, "worker_id": worker.id,
                      "completed": 2, "failed": 0},
                files={"file": ("out.jsonl", io.BytesIO(payload.encode()), "application/jsonl")},
            )
            assert res.status_code == 200, res.text
            assert res.json()["status"] == "completed"

        # TestClient runs background tasks before the context manager returns.
        db_session.expire_all()
        db_session.refresh(batch)
        assert batch.prompt_tokens == 15
        assert batch.completion_tokens == 27
        assert batch.total_tokens == 42

        rows = db_session.query(UsageRecord).filter(UsageRecord.batch_id == batch.id).all()
        assert {r.custom_id for r in rows} == {"req-1", "req-2"}
    finally:
        app.dependency_overrides.clear()


def _ingest_lines(batch_id, objects):
    """Write objects (dicts, or raw strings for malformed lines) and ingest."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8") as tmp:
        for o in objects:
            tmp.write((o if isinstance(o, str) else json.dumps(o)) + "\n")
        tmp_path = tmp.name
    try:
        ingest_usage_records(batch_id, tmp_path)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


_GOOD = {"custom_id": "good-1", "error": None,
         "response": {"usage": {"prompt_tokens": 4, "completion_tokens": 6, "total_tokens": 10}}}


@pytest.mark.parametrize("bad,reason", [
    ("{not json at all", "malformed JSON"),
    ('["a", "list"]', "valid JSON that is not an object"),
    ('null', "valid JSON null"),
    ({"custom_id": "bad", "error": None,
      "response": {"usage": {"prompt_tokens": "lots", "completion_tokens": 1}}},
     "non-numeric token count"),
    ({"custom_id": "bad", "error": None,
      "response": {"usage": {"prompt_tokens": {"nested": 1}, "completion_tokens": 1}}},
     "object where an int belongs"),
])
def test_one_bad_line_does_not_discard_the_rest(db_session, test_user, bad, reason):
    """A single unusable record must cost only itself, not the whole file."""
    batch = _create_test_batch(db_session, test_user)
    _ingest_lines(batch.id, [_GOOD, bad, {**_GOOD, "custom_id": "good-2"}])

    rows = db_session.query(UsageRecord).filter(UsageRecord.batch_id == batch.id).all()
    assert {r.custom_id for r in rows} == {"good-1", "good-2"}, f"lost records on: {reason}"

    db_session.refresh(batch)
    assert batch.total_tokens == 20


def test_failed_prompt_with_response_is_not_counted(db_session, test_user):
    """Ollama returns response AND error for JSON_PARSE_ERROR / SCHEMA_VIOLATION.

    The daemon counts those prompts as failed, so their tokens must not inflate
    the rollup.
    """
    batch = _create_test_batch(db_session, test_user)
    _ingest_lines(batch.id, [
        _GOOD,
        {"custom_id": "schema-violation-1",
         "error": "SCHEMA_VIOLATION: Response JSON violates requested schema",
         "response": {"usage": {"prompt_tokens": 999, "completion_tokens": 999,
                                "total_tokens": 1998}}},
    ])

    rows = db_session.query(UsageRecord).filter(UsageRecord.batch_id == batch.id).all()
    assert {r.custom_id for r in rows} == {"good-1"}

    db_session.refresh(batch)
    assert batch.prompt_tokens == 4
    assert batch.completion_tokens == 6
    assert batch.total_tokens == 10


def test_duplicate_custom_id_in_one_chunk_does_not_discard_the_file(db_session, test_user):
    """Two rows with the same custom_id must not cost the chunk around them.

    Postgres rejects an ON CONFLICT DO UPDATE whose VALUES hit the same
    conflict target twice, and that error would roll back every record
    buffered with it.
    """
    batch = _create_test_batch(db_session, test_user)
    dup = {"custom_id": "good-1", "error": None,
           "response": {"usage": {"prompt_tokens": 40, "completion_tokens": 60,
                                  "total_tokens": 100}}}
    _ingest_lines(batch.id, [_GOOD, dup, {**_GOOD, "custom_id": "good-2"}])

    rows = db_session.query(UsageRecord).filter(UsageRecord.batch_id == batch.id).all()
    assert {r.custom_id for r in rows} == {"good-1", "good-2"}

    # Last write wins, matching what the upsert does across two statements.
    by_id = {r.custom_id: r for r in rows}
    assert by_id["good-1"].total_tokens == 100

    db_session.refresh(batch)
    assert batch.total_tokens == 110
