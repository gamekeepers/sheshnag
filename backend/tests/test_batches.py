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
