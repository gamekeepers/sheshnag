"""Finetuning contract tests — /v1/fine_tuning/* (the user-facing API), the
dataset validator, and the /workers/finetune-* endpoints the runner uses.

Validation runs as a FastAPI background task, which TestClient completes
before returning; the fixtures below point it at the test DB and at a
whitespace token counter so no tokenizer is downloaded.
"""
import contextlib
import io
import json
import uuid
import zipfile

import pytest
from sqlalchemy.orm import sessionmaker

from models import (
    ApiKey, File as FileModel, FinetuneJob, FinetuneJobAssignment, Worker,
    Organization, OrganizationMembership,
)
from services import finetune_validator
from services.finetune_validator import validate_dataset


BASE = "qwen2-5-0-5b-instruct-hf"


def _ws_count(text):
    return len(text.split())


@pytest.fixture(autouse=True)
def seed_test_catalog(_engine):
    """Load the real catalogue manifest — same as test_models.py's fixture."""
    from catalog_seed import _load_manifest, _MANAGED_FIELDS
    from models import ModelCatalog

    db = sessionmaker(bind=_engine)()
    try:
        if db.query(ModelCatalog).count() > 0:
            return
        for entry in _load_manifest():
            if not entry.get("id"):
                continue
            fields = {k: entry.get(k) for k in _MANAGED_FIELDS}
            fields["enabled"] = bool(entry.get("enabled", True))
            db.add(ModelCatalog(id=entry["id"], **fields))
        db.commit()
    finally:
        db.close()


@pytest.fixture(autouse=True)
def release_finetune_assignments(_engine):
    """Other suites delete workers between tests; an assignment left behind
    here holds a foreign key to its worker and blocks that delete."""
    yield
    db = sessionmaker(bind=_engine)()
    try:
        db.query(FinetuneJobAssignment).delete()
        db.commit()
    finally:
        db.close()


@pytest.fixture(autouse=True)
def validator_uses_test_db(_engine, monkeypatch):
    monkeypatch.setattr(finetune_validator, "SessionLocal", sessionmaker(bind=_engine))
    monkeypatch.setattr(finetune_validator, "load_token_counter",
                        lambda ref, rev: (_ws_count, "test:whitespace"))


def _example(user="hi there", assistant="hello, how can I help"):
    return {"messages": [{"role": "user", "content": user},
                         {"role": "assistant", "content": assistant}]}


def _jsonl(rows):
    return "".join((r if isinstance(r, str) else json.dumps(r)) + "\n" for r in rows)


@pytest.fixture
def make_file(_engine, _test_user, tmp_path):
    def make(content: str, purpose="fine-tune", user_id=None):
        path = tmp_path / f"{uuid.uuid4().hex}.jsonl"
        path.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)
        db = sessionmaker(bind=_engine, expire_on_commit=False)()
        try:
            rec = FileModel(user_id=user_id or _test_user.id, filename=path.name,
                            purpose=purpose, bytes=path.stat().st_size, filepath=str(path))
            db.add(rec)
            db.commit()
            return rec.id
        finally:
            db.close()
    return make


@pytest.fixture
def good_file(make_file):
    return make_file(_jsonl([_example() for _ in range(12)]))


def _create(client, file_id, **extra):
    return client.post("/v1/fine_tuning/jobs", json={"model": BASE, "training_file": file_id, **extra})


# ─── Creating a job ────────────────────────────────────────

def _own_org_id(engine, user):
    db = sessionmaker(bind=engine)()
    try:
        return db.query(OrganizationMembership).filter(
            OrganizationMembership.user_id == user.id).first().org_id
    finally:
        db.close()


@contextlib.contextmanager
def _client_for(engine, user):
    """A client authenticated as `user` with a personal gk- key."""
    from main import app
    from auth import hash_api_key
    from database import get_db
    from fastapi.testclient import TestClient
    from tests.conftest import _make_override

    raw = f"gk-{uuid.uuid4().hex}"
    db = sessionmaker(bind=engine)()
    db.add(ApiKey(key_type="personal", created_by_user_id=user.id, name="t",
                  key_prefix=raw[:8], key_hash=hash_api_key(raw)))
    db.commit()
    db.close()
    app.dependency_overrides[get_db] = _make_override(engine)
    try:
        with TestClient(app) as client:
            client.headers["Authorization"] = f"Bearer {raw}"
            yield client
    finally:
        app.dependency_overrides.clear()


def _fresh_user(engine):
    from tests.conftest import _seed_user
    return _seed_user(engine, f"ft-{uuid.uuid4().hex[:8]}@example.com", "testpassword123", "Org Test")


def test_create_records_the_callers_only_org(_engine, make_file):
    user = _fresh_user(_engine)
    file_id = make_file(_jsonl([_example() for _ in range(12)]), user_id=user.id)
    with _client_for(_engine, user) as client:
        res = _create(client, file_id)
    assert res.status_code == 200, res.text
    assert res.json()["organization_id"] == _own_org_id(_engine, user)


def test_create_records_no_org_when_the_caller_has_several_and_names_none(_engine, make_file):
    user = _fresh_user(_engine)
    db = sessionmaker(bind=_engine, expire_on_commit=False)()
    second = Organization(name="Second org")
    db.add(second)
    db.flush()
    db.add(OrganizationMembership(org_id=second.id, user_id=user.id, role="admin"))
    db.commit()
    db.close()
    file_id = make_file(_jsonl([_example() for _ in range(12)]), user_id=user.id)
    with _client_for(_engine, user) as client:
        res = _create(client, file_id)
    assert res.status_code == 200, res.text
    assert res.json()["organization_id"] is None


def test_create_records_a_named_org_the_caller_belongs_to(auth_client, good_file, _engine, _test_user):
    org_id = _own_org_id(_engine, _test_user)
    res = _create(auth_client, good_file, organization_id=org_id)
    assert res.status_code == 200, res.text
    assert res.json()["organization_id"] == org_id


def test_create_refuses_an_org_the_caller_is_not_in(auth_client, good_file, _engine):
    db = sessionmaker(bind=_engine, expire_on_commit=False)()
    other = Organization(name="Another org")
    db.add(other)
    db.commit()
    try:
        res = _create(auth_client, good_file, organization_id=other.id)
        assert res.status_code == 403
        assert "not a member" in res.json()["detail"]
    finally:
        db.delete(other)
        db.commit()
        db.close()


def test_create_refuses_an_unknown_org(auth_client, good_file):
    res = _create(auth_client, good_file, organization_id="org-does-not-exist")
    assert res.status_code == 400
    assert "not found" in res.json()["detail"]


def test_create_validates_and_queues(auth_client, good_file):
    res = _create(auth_client, good_file, suffix="support-bot")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["object"] == "fine_tuning.job"
    assert body["status"] == "validating_files"
    assert body["base_model"]["source_ref"] == "Qwen/Qwen2.5-0.5B-Instruct"
    assert body["lora"] == {"rank": 16, "alpha": 32, "dropout": 0.05, "target_modules": "all-linear"}
    assert body["engine"]["name"] == "llamafactory"

    job = auth_client.get(f"/v1/fine_tuning/jobs/{body['id']}").json()
    assert job["status"] == "queued"
    stats = job["training_file_stats"]
    assert stats["n_examples"] == 12
    assert stats["token_count_method"] == "test:whitespace"
    assert sum(b["count"] for b in stats["histogram"]) == 12

    events = auth_client.get(f"/v1/fine_tuning/jobs/{body['id']}/events").json()["data"]
    messages = [e["message"] for e in events]
    assert messages[0] == "Job queued for training"  # newest first
    assert any("12 examples" in m for m in messages)


def test_personal_api_key_works(_engine, _test_user, good_file):
    """Same API key as /v1/files and /v1/batches — a personal gk- key."""
    from main import app
    from auth import hash_api_key
    from database import get_db
    from fastapi.testclient import TestClient
    from tests.conftest import _make_override

    raw = f"gk-{uuid.uuid4().hex}"
    db = sessionmaker(bind=_engine)()
    db.add(ApiKey(key_type="personal", created_by_user_id=_test_user.id, name="t",
                  key_prefix=raw[:8], key_hash=hash_api_key(raw)))
    db.commit()
    db.close()

    app.dependency_overrides[get_db] = _make_override(_engine)
    try:
        with TestClient(app) as client:
            client.headers["Authorization"] = f"Bearer {raw}"
            res = _create(client, good_file)
            assert res.status_code == 200, res.text
            assert client.get("/v1/fine_tuning/jobs").status_code == 200
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("payload, needle", [
    ({"lora": {"rank": 128}}, "max-lora-rank"),
    ({"lora": {"rank": 12}}, "rank must be one of"),
    ({"lora": {"rnk": 16}}, "Extra inputs are not permitted"),            # typos must not pass silently
    ({"hyperparameters": {"n_epoch": 3}}, "Extra inputs are not permitted"),
    ({"lora": {"target_modules": ["q_proj", "lm_head"]}}, "not servable"),
    ({"method": {"type": "dpo"}}, "supervised"),
    ({"suffix": "Has Spaces"}, "suffix"),
])
def test_create_rejects_bad_contract(auth_client, good_file, payload, needle):
    res = _create(auth_client, good_file, **payload)
    assert res.status_code == 422
    assert needle in res.text


def test_create_normalises_target_module_list(auth_client, good_file):
    res = _create(auth_client, good_file, lora={"target_modules": ["v_proj", "q_proj", "q_proj"]})
    assert res.json()["lora"]["target_modules"] == ["q_proj", "v_proj"]


def test_create_rejects_ollama_base_model(auth_client, good_file):
    res = auth_client.post("/v1/fine_tuning/jobs",
                           json={"model": "nuextract-3-8b-ollama", "training_file": good_file})
    assert res.status_code == 400
    assert "not a finetune-eligible" in res.json()["detail"]


def test_create_rejects_unpinned_base_model(auth_client, good_file, db_session):
    """A base on a branch ("main") could move under a trained adapter."""
    from models import ModelCatalog
    entry = db_session.query(ModelCatalog).filter(ModelCatalog.id == BASE).first()
    pinned = entry.source_revision
    assert len(pinned) == 40
    entry.source_revision = "main"
    db_session.commit()
    try:
        res = _create(auth_client, good_file)
        assert res.status_code == 400
        assert "not a commit SHA" in res.json()["detail"]
    finally:
        entry.source_revision = pinned
        db_session.commit()


def test_create_rejects_base_without_chat_template(auth_client, good_file, db_session):
    """forge-runner can only train bases with a curated chat template; a job
    on any other base must be refused here, not after queueing and a claim."""
    from models import ModelCatalog
    entry = db_session.query(ModelCatalog).filter(ModelCatalog.id == BASE).first()
    template = entry.chat_template
    assert template == "qwen"
    entry.chat_template = None
    db_session.commit()
    try:
        res = _create(auth_client, good_file)
        assert res.status_code == 400
        detail = res.json()["detail"]
        assert "no chat template" in detail
        assert "tinyllama-1-1b-chat-hf" in detail       # names what *is* trainable
        assert BASE not in detail.split("Finetunable bases:")[1]
    finally:
        entry.chat_template = template
        db_session.commit()


def test_create_rejects_wrong_file_purpose(auth_client, make_file):
    file_id = make_file(_jsonl([_example()] * 12), purpose="batch")
    res = _create(auth_client, file_id)
    assert res.status_code == 400
    assert "purpose='fine-tune'" in res.json()["detail"]


def test_create_rejects_unknown_file(auth_client):
    assert _create(auth_client, "file-nope").status_code == 400


def test_get_not_found(auth_client):
    assert auth_client.get("/v1/fine_tuning/jobs/ftjob-nope").status_code == 404


def test_list_paginates(auth_client, good_file):
    ids = [_create(auth_client, good_file).json()["id"] for _ in range(3)]
    seen, url = [], "/v1/fine_tuning/jobs?limit=2"
    while True:
        page = auth_client.get(url).json()
        assert len(page["data"]) <= 2
        seen += [j["id"] for j in page["data"]]
        if not page["has_more"]:
            break
        url = f"/v1/fine_tuning/jobs?limit=2&after={page['data'][-1]['id']}"
    assert set(ids) <= set(seen) and len(seen) == len(set(seen))


def test_cancel(auth_client, good_file):
    job_id = _create(auth_client, good_file).json()["id"]
    res = auth_client.post(f"/v1/fine_tuning/jobs/{job_id}/cancel")
    assert res.status_code == 200 and res.json()["status"] == "cancelled"
    assert auth_client.post(f"/v1/fine_tuning/jobs/{job_id}/cancel").status_code == 409


# ─── Validator: malformed files fail with a fix ────────────

@pytest.mark.parametrize("content, code, hint_needle", [
    (_jsonl([{"prompt": "a", "completion": "b"}] * 12), "wrong_format", '"role": "user", "content": <prompt>'),
    (_jsonl([{"conversations": [{"from": "human", "value": "a"}]}] * 12), "wrong_format", "human -> user"),
    (json.dumps([_example()] * 12), "json_array", "one JSON object per line"),
    (json.dumps(_example(), indent=2) + "\n", "invalid_json", "pretty-printed"),
    (_jsonl([{"messages": [{"role": "human", "content": "a"},
                           {"role": "assistant", "content": "b"}]}] * 12), "invalid_role", 'change "human" to "user"'),
    (_jsonl([{"messages": [{"role": "user", "content": "a"},
                           {"role": "assistant", "content": "b"},
                           {"role": "user", "content": "c"}]}] * 12), "last_not_assistant", "end each example"),
    (_jsonl([{"messages": [{"role": "user", "content": "a"},
                           {"role": "system", "content": "s"},
                           {"role": "assistant", "content": "b"}]}] * 12), "misplaced_system", "move the system"),
    (_jsonl([_example()] * 3), "too_few_examples", "10 is the minimum"),
    ("", "empty_file", "add at least"),
    ("\n".join(["{'messages': []}"] * 12), "invalid_json", "single quotes"),
])
def test_validator_rejects_with_hint(tmp_path, content, code, hint_needle):
    path = tmp_path / "d.jsonl"
    path.write_text(content, encoding="utf-8")
    result = validate_dataset(str(path), _ws_count)
    assert not result.valid
    err = result.errors[0]
    assert err.code == code, result.errors
    assert hint_needle in err.hint


def test_validator_rejects_non_utf8(tmp_path):
    path = tmp_path / "d.jsonl"
    path.write_bytes(_jsonl([_example()] * 12).encode("utf-8") + b"\xff\xfe bad\n")
    result = validate_dataset(str(path), _ws_count)
    assert result.errors[0].code == "not_utf8"
    assert "UTF-8" in result.errors[0].hint


def test_validator_accepts_bom_and_blank_lines(tmp_path):
    path = tmp_path / "d.jsonl"
    path.write_bytes(b"\xef\xbb\xbf" + (_jsonl([_example()] * 12) + "\n\n").encode("utf-8"))
    assert validate_dataset(str(path), _ws_count).valid


def test_validator_counts_tokens_and_builds_histogram(tmp_path):
    short = _example("a", "b")                  # 1 + 1 words + 2*5 template = 12 tokens
    long = _example("word " * 300, "b")         # 300 + 1 + 10 = 311 tokens
    path = tmp_path / "d.jsonl"
    path.write_text(_jsonl([short] * 10 + [long] * 2), encoding="utf-8")

    result = validate_dataset(str(path), _ws_count)
    assert result.valid
    stats = result.stats(max_seq_length=256, token_count_method="test")
    assert stats["n_examples"] == 12
    assert stats["total_tokens"] == 10 * 12 + 2 * 311
    assert (stats["min"], stats["max"]) == (12, 311)
    assert stats["n_truncated"] == 2
    buckets = {(b["gt"], b["le"]): b["count"] for b in stats["histogram"]}
    assert buckets[(0, 128)] == 10 and buckets[(256, 512)] == 2


def test_malformed_file_fails_job_with_actionable_error(auth_client, make_file):
    file_id = make_file(_jsonl([{"prompt": "a", "completion": "b"}] * 12))
    job_id = _create(auth_client, file_id).json()["id"]

    job = auth_client.get(f"/v1/fine_tuning/jobs/{job_id}").json()
    assert job["status"] == "failed"
    assert job["error"]["code"] == "invalid_training_file"
    assert job["error"]["param"] == "training_file"
    assert "line 1:" in job["error"]["message"] and "fix:" in job["error"]["message"]
    assert job["error"]["total_errors"] == 12
    assert job["error"]["details"][0]["hint"]


# ─── Worker side: poll -> progress -> adapter ──────────────

@pytest.fixture
def worker_client(_engine, _test_user):
    """TestClient with a worker gk- key, plus a registered worker in the test user's org."""
    from main import app
    from auth import hash_api_key
    from database import get_db
    from fastapi.testclient import TestClient
    from tests.conftest import _make_override

    db = sessionmaker(bind=_engine, expire_on_commit=False)()
    org_id = db.query(OrganizationMembership).filter(
        OrganizationMembership.user_id == _test_user.id).first().org_id
    raw = f"gk-{uuid.uuid4().hex}"
    db.add(ApiKey(key_type="worker", org_id=org_id, created_by_user_id=_test_user.id,
                  name="w", key_prefix=raw[:8], key_hash=hash_api_key(raw)))
    worker = Worker(org_id=org_id, hostname="ft-worker", status="online",
                    vram_total_gb=48, vram_available_gb=48)
    db.add(worker)
    # Isolate from jobs other tests left queued.
    db.query(FinetuneJob).filter(FinetuneJob.status == "queued").update({"status": "cancelled"})
    db.commit()
    db.close()

    app.dependency_overrides[get_db] = _make_override(_engine)
    with TestClient(app) as client:
        client.headers["Authorization"] = f"Bearer {raw}"
        client.worker_id = worker.id
        yield client
    app.dependency_overrides.clear()


def _adapter_zip(**config_overrides):
    config = {"peft_type": "LORA", "r": 16, "lora_alpha": 32,
              "base_model_name_or_path": "Qwen/Qwen2.5-0.5B-Instruct",
              "target_modules": ["down_proj", "gate_proj", "k_proj", "o_proj", "q_proj", "up_proj", "v_proj"],
              "modules_to_save": None}
    config.update(config_overrides)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("adapter/adapter_config.json", json.dumps(config))
        zf.writestr("adapter/adapter_model.safetensors", b"weights")
    return buf.getvalue()


def _claim(auth_client, worker_client, file_id):
    job_id = _create(auth_client, file_id).json()["id"]
    job = worker_client.post("/workers/finetune-poll", json={"worker_id": worker_client.worker_id}).json()["job"]
    assert job["job_id"] == job_id
    return job


def _upload(worker_client, job_id, data):
    return worker_client.post(
        "/workers/finetune-upload-adapter",
        data={"job_id": job_id, "worker_id": worker_client.worker_id,
              "engine": "llamafactory", "engine_version": "0.9.5",
              "metrics": json.dumps({"train_loss": 2.4})},
        files={"file": ("adapter.zip", data, "application/zip")},
    )


def test_worker_full_run_produces_servable_adapter(auth_client, worker_client, good_file):
    job = _claim(auth_client, worker_client, good_file)
    assert job["source_ref"] == "Qwen/Qwen2.5-0.5B-Instruct"
    assert job["target_modules"] == "all-linear" and job["engine"] == "llamafactory"
    assert job["chat_template"] == "qwen"
    assert auth_client.get(f"/v1/fine_tuning/jobs/{job['job_id']}").json()["status"] == "running"

    # The worker can download its training file.
    assert worker_client.get(job["dataset_path"]).status_code == 200

    res = worker_client.post("/workers/finetune-progress", json={
        "job_id": job["job_id"], "worker_id": worker_client.worker_id,
        "current_step": 5, "total_steps": 29, "current_epoch": 0.17, "loss": 2.51})
    assert res.json()["stop"] is False

    res = _upload(worker_client, job["job_id"], _adapter_zip())
    assert res.status_code == 200, res.text
    assert res.json()["servable"] is True

    done = auth_client.get(f"/v1/fine_tuning/jobs/{job['job_id']}").json()
    assert done["status"] == "succeeded"
    assert done["fine_tuned_model"].startswith(f"ft:{BASE}:")
    assert done["result_files"] == [res.json()["output_adapter_file_id"]]
    assert done["trained_tokens"] == done["training_file_stats"]["total_tokens"] * 3

    art = auth_client.get(f"/v1/fine_tuning/jobs/{job['job_id']}/artifacts").json()["data"][0]
    assert art["lora_rank"] == 16 and art["servable"] is True
    assert art["base_model"]["source_ref"] == "Qwen/Qwen2.5-0.5B-Instruct"
    assert len(art["sha256"]) == 64 and art["metrics"] == {"train_loss": 2.4}

    events = auth_client.get(f"/v1/fine_tuning/jobs/{job['job_id']}/events?limit=100").json()["data"]
    metrics = [e for e in events if e["type"] == "metrics"]
    assert metrics[0]["data"]["loss"] == 2.51


@pytest.mark.parametrize("overrides, reason", [
    ({"r": 128}, "max-lora-rank"),
    ({"base_model_name_or_path": "Qwen/Qwen2.5-0.5B"}, "trained on"),   # shape-identical wrong base
    ({"target_modules": ["q_proj", "lm_head"]}, "not proven servable"),
    ({"modules_to_save": ["lm_head"]}, "modules_to_save"),
])
def test_unservable_adapter_fails_job(auth_client, worker_client, good_file, overrides, reason):
    job = _claim(auth_client, worker_client, good_file)
    res = _upload(worker_client, job["job_id"], _adapter_zip(**overrides))
    assert res.status_code == 200
    assert res.json()["servable"] is False

    done = auth_client.get(f"/v1/fine_tuning/jobs/{job['job_id']}").json()
    assert done["status"] == "failed"
    assert done["error"]["code"] == "unservable_adapter"
    assert reason in done["error"]["message"]
    assert done["fine_tuned_model"] is None


def test_upload_rejects_non_adapter_zip(auth_client, worker_client, good_file):
    job = _claim(auth_client, worker_client, good_file)
    res = _upload(worker_client, job["job_id"], b"not a zip")
    assert res.status_code == 400
    assert auth_client.get(f"/v1/fine_tuning/jobs/{job['job_id']}").json()["status"] == "running"


def test_cancel_running_job_tells_worker_to_stop(auth_client, worker_client, good_file):
    job = _claim(auth_client, worker_client, good_file)
    auth_client.post(f"/v1/fine_tuning/jobs/{job['job_id']}/cancel")
    res = worker_client.post("/workers/finetune-progress", json={
        "job_id": job["job_id"], "worker_id": worker_client.worker_id,
        "current_step": 1, "total_steps": 29, "current_epoch": 0.03})
    assert res.json()["stop"] is True
    assert _upload(worker_client, job["job_id"], _adapter_zip()).status_code == 409


def test_failure_requeues_then_fails(auth_client, worker_client, good_file):
    job = _claim(auth_client, worker_client, good_file)
    job_id = job["job_id"]
    for attempt in (1, 2, 3):
        res = worker_client.post("/workers/finetune-report-failure", json={
            "job_id": job_id, "worker_id": worker_client.worker_id, "error": "CUDA out of memory"})
        assert res.json()["attempts"] == attempt
        if attempt < 3:
            assert res.json()["status"] == "queued"
            worker_client.post("/workers/finetune-poll", json={"worker_id": worker_client.worker_id})

    done = auth_client.get(f"/v1/fine_tuning/jobs/{job_id}").json()
    assert done["status"] == "failed"
    assert done["error"]["code"] == "training_failed"
    assert "CUDA out of memory" in done["error"]["message"]


def test_non_retryable_failure_fails_immediately(auth_client, worker_client, good_file):
    job = _claim(auth_client, worker_client, good_file)
    res = worker_client.post("/workers/finetune-report-failure", json={
        "job_id": job["job_id"], "worker_id": worker_client.worker_id,
        "error": "The engine rejected the generated config", "code": "runner_config_rejected",
        "retryable": False})
    assert res.json()["status"] == "failed" and res.json()["attempts"] == 1
    done = auth_client.get(f"/v1/fine_tuning/jobs/{job['job_id']}").json()
    assert done["error"]["code"] == "runner_config_rejected"


# ─── Training is never restarted from step 0 behind the user's back ───

def _age_worker(_engine, worker_id, seconds):
    from models import Worker, unix_now
    db = sessionmaker(bind=_engine)()
    db.query(Worker).filter(Worker.id == worker_id).update(
        {"last_heartbeat": unix_now() - seconds, "status": "online"})
    db.commit()
    db.close()


def test_worker_sweep_leaves_running_finetune_job_alone(_engine, auth_client, worker_client, good_file):
    from sweeper import sweep_stale_workers, HEARTBEAT_TIMEOUT_SECONDS
    job = _claim(auth_client, worker_client, good_file)
    _age_worker(_engine, worker_client.worker_id, HEARTBEAT_TIMEOUT_SECONDS + 60)

    db = sessionmaker(bind=_engine)()
    sweep_stale_workers(db)
    db.close()

    assert auth_client.get(f"/v1/fine_tuning/jobs/{job['job_id']}").json()["status"] == "running"


def test_lost_runner_fails_job_instead_of_requeueing(_engine, auth_client, worker_client, good_file):
    from sweeper import fail_lost_finetune_jobs, FINETUNE_RUNNER_LOST_SECONDS
    job = _claim(auth_client, worker_client, good_file)

    db = sessionmaker(bind=_engine)()
    assert fail_lost_finetune_jobs(db) == 0          # runner still heartbeating
    _age_worker(_engine, worker_client.worker_id, FINETUNE_RUNNER_LOST_SECONDS + 60)
    assert fail_lost_finetune_jobs(db) == 1
    db.close()

    done = auth_client.get(f"/v1/fine_tuning/jobs/{job['job_id']}").json()
    assert done["status"] == "failed"
    assert done["error"]["code"] == "runner_lost"
    assert "resubmit" in done["error"]["message"]
    assert "ft-worker" not in done["error"]["message"]          # no provider hostname
    assert "ft-worker" not in json.dumps(
        auth_client.get(f"/v1/fine_tuning/jobs/{job['job_id']}/events?limit=100").json())


@pytest.mark.parametrize("vram_free, claims", [
    (4.0, False),    # 48 GB card with 4 GB free: 1.5 GB x 3.2 = 4.8 GB needed
    (48.0, True),
    (0.0, True),     # worker could not measure VRAM: unknown, not "zero"
    (None, True),    # no heartbeat yet
])
def test_picker_vram_fit(_engine, auth_client, worker_client, good_file, vram_free, claims):
    """Fit is against free VRAM: the card's total says nothing about room
    for training on a shared GPU."""
    from models import Worker
    db = sessionmaker(bind=_engine)()
    db.query(Worker).filter(Worker.id == worker_client.worker_id).update(
        {"vram_total_gb": 48.0, "vram_available_gb": vram_free})
    db.commit()
    db.close()
    _create(auth_client, good_file)
    job = worker_client.post("/workers/finetune-poll", json={"worker_id": worker_client.worker_id}).json()["job"]
    assert (job is not None) == claims


@contextlib.contextmanager
def _worker_in_new_org(engine):
    """A worker client registered in a brand-new org (its own user and key)."""
    from main import app
    from auth import hash_api_key
    from database import get_db
    from fastapi.testclient import TestClient
    from tests.conftest import _make_override

    user = _fresh_user(engine)
    org_id = _own_org_id(engine, user)
    raw = f"gk-{uuid.uuid4().hex}"
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    db.add(ApiKey(key_type="worker", org_id=org_id, created_by_user_id=user.id,
                  name="w2", key_prefix=raw[:8], key_hash=hash_api_key(raw)))
    worker = Worker(org_id=org_id, hostname="other-worker", status="online",
                    vram_total_gb=48, vram_available_gb=48)
    db.add(worker)
    db.commit()
    db.close()
    app.dependency_overrides[get_db] = _make_override(engine)
    with TestClient(app) as client:
        client.headers["Authorization"] = f"Bearer {raw}"
        client.worker_id = worker.id
        yield client


def test_shared_training_file_readable_by_each_assigned_worker(_engine, auth_client, worker_client, good_file):
    """Two jobs on the same dataset (a hyperparameter rerun), claimed by
    workers from different orgs: each worker can read its own job's data,
    and a worker with no assignment cannot."""
    first = _claim(auth_client, worker_client, good_file)
    second_id = _create(auth_client, good_file, seed=7).json()["id"]

    with _worker_in_new_org(_engine) as other:
        second = other.post("/workers/finetune-poll", json={"worker_id": other.worker_id}).json()["job"]
        assert second["job_id"] == second_id
        assert second["dataset_path"] == first["dataset_path"]
        assert other.get(second["dataset_path"]).status_code == 200
    assert worker_client.get(first["dataset_path"]).status_code == 200

    with _worker_in_new_org(_engine) as stranger:
        assert stranger.get(first["dataset_path"]).status_code == 403
