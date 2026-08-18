"""File deletion API tests (DELETE /v1/files/{id})."""

import pytest
from sqlalchemy.orm import sessionmaker

from models import Batch, File as FileModel


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


def test_list_files_returns_only_own(auth_client, _engine, _test_user, _test_superuser, tmp_path):
    mine, _ = _seed_file(_engine, _test_user.id, tmp_path, name="mine.jsonl")
    theirs, _ = _seed_file(_engine, _test_superuser.id, tmp_path, name="theirs.jsonl")

    res = auth_client.get("/v1/files")
    assert res.status_code == 200, res.text
    ids = [f["id"] for f in res.json()["data"]]
    assert mine.id in ids
    assert theirs.id not in ids


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


@pytest.mark.parametrize("status", ["validating", "validated", "in_progress"])
def test_delete_file_with_active_batch_409(auth_client, _engine, _test_user, tmp_path, status):
    record, jsonl = _seed_file(_engine, _test_user.id, tmp_path, name=f"active-{status}.jsonl")

    # Seeded directly rather than via POST /v1/batches. Creation fires a
    # fire-and-forget validation task (routers/batches.py:58) that writes the
    # batch's final status from a worker thread, so a batch made through the
    # API can reach `failed` — not an active status — before the delete lands.
    # Creation itself is covered in test_batches.py; the guard is what this
    # test is about, and it should not race a background thread to assert it.
    SM = sessionmaker(bind=_engine, expire_on_commit=False)
    db = SM()
    try:
        db.add(Batch(
            user_id=_test_user.id,
            endpoint="/v1/chat/completions",
            input_file_id=record.id,
            completion_window="24h",
            status=status,
        ))
        db.commit()
    finally:
        db.close()

    res = auth_client.delete(f"/v1/files/{record.id}")
    assert res.status_code == 409
    assert "batch" in res.json()["detail"]
    assert jsonl.exists()
