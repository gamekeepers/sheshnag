"""File deletion API tests (DELETE /v1/files/{id})."""

import pytest
from sqlalchemy.orm import sessionmaker

from models import File as FileModel


def _seed_file(_engine, owner_id, tmp_path, name="input.jsonl"):
    jsonl = tmp_path / name
    jsonl.write_text(
        '{"custom_id": "req-1", "method": "POST", "url": "/v1/chat/completions", '
        '"body": {"model": "llama3:8b", "messages": [{"role": "user", "content": "hi"}]}}\n'
    )
    SM = sessionmaker(bind=_engine, expire_on_commit=False)
    db = SM()
    try:
        record = FileModel(
            user_id=owner_id,
            filename=name,
            purpose="batch",
            bytes=jsonl.stat().st_size,
            filepath=str(jsonl),
        )
        db.add(record)
        db.commit()
        db.refresh(record)
        return record, jsonl
    finally:
        db.close()


def test_delete_own_file(auth_client, _engine, _test_user, tmp_path, db_session):
    record, jsonl = _seed_file(_engine, _test_user.id, tmp_path)

    res = auth_client.delete(f"/v1/files/{record.id}")
    assert res.status_code == 200, res.text
    assert res.json() == {"id": record.id, "object": "file", "deleted": True}
    assert not jsonl.exists()
    assert db_session.query(FileModel).filter(FileModel.id == record.id).first() is None


def test_delete_unknown_file_404(auth_client):
    assert auth_client.delete("/v1/files/file-does-not-exist").status_code == 404


def test_delete_someone_elses_file_403(auth_client, _engine, _test_superuser, tmp_path):
    record, jsonl = _seed_file(_engine, _test_superuser.id, tmp_path, name="other.jsonl")

    res = auth_client.delete(f"/v1/files/{record.id}")
    assert res.status_code == 403
    assert jsonl.exists()


def test_delete_file_with_active_batch_409(auth_client, _engine, _test_user, tmp_path):
    record, jsonl = _seed_file(_engine, _test_user.id, tmp_path, name="active.jsonl")

    res = auth_client.post("/v1/batches", json={
        "input_file_id": record.id,
        "endpoint": "/v1/chat/completions",
        "completion_window": "24h",
    })
    assert res.status_code == 200, res.text

    res = auth_client.delete(f"/v1/files/{record.id}")
    assert res.status_code == 409
    assert "batch" in res.json()["detail"]
    assert jsonl.exists()
