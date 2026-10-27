"""Finetune job API tests — dataset validation gate, base-model eligibility
gate, and the human-facing CRUD surface. Mirrors test_batches.py's scope:
the worker-facing endpoints in workers.py (finetune-poll/-progress/
-upload-adapter/-report-failure) have no pytest coverage here, matching
the existing convention that workers.py's batch endpoints are exercised
by the real daemon rather than unit-tested.
"""

import pytest
from sqlalchemy.orm import sessionmaker

from models import File as FileModel


@pytest.fixture(autouse=True)
def seed_test_catalog(_engine):
    """Load the real catalogue manifest into the test DB — same pattern as
    test_models.py's fixture of the same name, so qwen2-5-0-5b-instruct-hf
    and the ollama-library entries both exist for the eligibility-gate
    tests below."""
    from catalog_seed import _load_manifest, _MANAGED_FIELDS
    from models import ModelCatalog

    SM = sessionmaker(bind=_engine)
    db = SM()
    try:
        if db.query(ModelCatalog).count() > 0:
            return
        entries = _load_manifest()
        for entry in entries:
            mid = entry.get("id")
            if not mid:
                continue
            fields = {k: entry.get(k) for k in _MANAGED_FIELDS}
            fields["enabled"] = bool(entry.get("enabled", True))
            db.add(ModelCatalog(id=mid, **fields))
        db.commit()
    finally:
        db.close()


@pytest.fixture
def seeded_dataset_file(_engine, _test_user, tmp_path):
    """A valid finetune-dataset JSONL (messages format) owned by the test user."""
    jsonl = tmp_path / "dataset.jsonl"
    jsonl.write_text(
        '{"messages": [{"role": "user", "content": "hi"}, '
        '{"role": "assistant", "content": "hello"}]}\n'
    )

    SM = sessionmaker(bind=_engine, expire_on_commit=False)
    db = SM()
    try:
        record = FileModel(
            user_id=_test_user.id,
            filename="dataset.jsonl",
            purpose="finetune_dataset",
            bytes=jsonl.stat().st_size,
            filepath=str(jsonl),
        )
        db.add(record)
        db.commit()
        db.refresh(record)
        return record
    finally:
        db.close()


def _job_payload(file_id, base_model_id="qwen2-5-0-5b-instruct-hf", **extra):
    payload = {"dataset_file_id": file_id, "base_model_id": base_model_id}
    payload.update(extra)
    return payload


def test_create_finetune_job_valid(auth_client, seeded_dataset_file):
    res = auth_client.post("/v1/finetune-jobs", json=_job_payload(seeded_dataset_file.id))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "validating"
    assert body["dataset_file_id"] == seeded_dataset_file.id
    assert body["base_model_id"] == "qwen2-5-0-5b-instruct-hf"
    assert body["lora_rank"] == 8  # default


def test_create_finetune_job_custom_hyperparams(auth_client, seeded_dataset_file):
    res = auth_client.post(
        "/v1/finetune-jobs",
        json=_job_payload(seeded_dataset_file.id, lora_rank=16, epochs=5, learning_rate=1e-4),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["lora_rank"] == 16
    assert body["epochs"] == 5
    assert body["learning_rate"] == 1e-4


def test_create_finetune_job_rejects_ollama_base_model(auth_client, seeded_dataset_file):
    """An Ollama-library entry is a pre-quantized serving artifact, not raw
    weights a training loop can load — must be rejected at creation, not
    discovered later when a worker can't run it."""
    res = auth_client.post(
        "/v1/finetune-jobs",
        json=_job_payload(seeded_dataset_file.id, base_model_id="nuextract-3-8b-ollama"),
    )
    assert res.status_code == 400
    assert "not a finetune-eligible" in res.json()["detail"]


def test_create_finetune_job_rejects_unknown_base_model(auth_client, seeded_dataset_file):
    res = auth_client.post(
        "/v1/finetune-jobs",
        json=_job_payload(seeded_dataset_file.id, base_model_id="does-not-exist"),
    )
    assert res.status_code == 400


def test_create_finetune_job_unknown_file_rejected(auth_client):
    res = auth_client.post("/v1/finetune-jobs", json=_job_payload("file-does-not-exist"))
    assert res.status_code == 400


def test_list_finetune_jobs(auth_client, seeded_dataset_file):
    auth_client.post("/v1/finetune-jobs", json=_job_payload(seeded_dataset_file.id))
    res = auth_client.get("/v1/finetune-jobs")
    assert res.status_code == 200
    data = res.json()["data"]
    assert any(j["dataset_file_id"] == seeded_dataset_file.id for j in data)


def test_get_finetune_job(auth_client, seeded_dataset_file):
    create_res = auth_client.post("/v1/finetune-jobs", json=_job_payload(seeded_dataset_file.id))
    job_id = create_res.json()["id"]

    res = auth_client.get(f"/v1/finetune-jobs/{job_id}")
    assert res.status_code == 200
    assert res.json()["id"] == job_id


def test_get_finetune_job_not_found(auth_client):
    res = auth_client.get("/v1/finetune-jobs/ftjob-does-not-exist")
    assert res.status_code == 404
