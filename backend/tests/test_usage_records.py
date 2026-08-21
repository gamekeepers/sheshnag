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


def test_worker_progress_updates_token_rollups(db_session, test_user, _engine):
    from fastapi.testclient import TestClient
    from main import app
    from auth import hash_api_key
    from models import ApiKey, Worker, BatchAssignment
    from database import get_db
    from tests.conftest import _make_override

    # Create worker API key, worker, batch, and batch assignment
    key_val = "gk-test-worker-key-12345678"
    key_hash = hash_api_key(key_val)

    org = Organization(name="Worker Progress Org")
    db_session.add(org)
    db_session.flush()

    api_key = ApiKey(
        org_id=org.id,
        key_type="worker",
        created_by_user_id=test_user.id,
        name="Test Worker Key",
        key_prefix=key_val[:8],
        key_hash=key_hash,
        status="active",
    )
    db_session.add(api_key)
    db_session.flush()

    worker = Worker(org_id=org.id, hostname="worker-progress-1", status="online")
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

    assignment = BatchAssignment(
        batch_id=batch.id,
        worker_id=worker.id,
        org_id=org.id,
        worker_hostname=worker.hostname,
    )
    db_session.add(assignment)
    db_session.commit()

    override = _make_override(_engine)
    app.dependency_overrides[get_db] = override

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
                    "prompt_tokens": 100,
                    "completion_tokens": 250,
                    "total_tokens": 350,
                },
            )
            assert res.status_code == 200
            assert res.json()["status"] == "ok"

            db_session.refresh(batch)
            assert batch.request_counts_completed == 5
            assert batch.prompt_tokens == 100
            assert batch.completion_tokens == 250
            assert batch.total_tokens == 350
    finally:
        app.dependency_overrides.clear()

