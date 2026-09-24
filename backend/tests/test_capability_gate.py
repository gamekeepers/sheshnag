"""Runtime capabilities: advertised at registration, required by rows,
honoured by the validator and the scheduler (#138).

A logprobs row on a pool whose only host for the model is an Ollama below
0.12.11 must fail at validation with a reason, not come back hours later as
a 200 without logprobs. The same row on a pool with no host online must
queue, because a capable worker may still arrive.
"""

import io
import json
import time

from sqlalchemy.orm import sessionmaker

from models import Batch, WorkerRuntime
from tests.test_worker_poll import _catalog, _reset, _worker_key


def _register(auth_client, key, *, hostname, capabilities, model="poll-model:latest"):
    resp = auth_client.post(
        "/workers/register",
        json={
            "hostname": hostname,
            "gpus": [{"index": 0, "name": "RTX 4090", "vram_gb": 24.0}],
            "runtimes": [{
                "type": "ollama", "endpoint": "localhost", "models": [model],
                "capabilities": capabilities, "version": "0.32.14",
            }],
        },
        headers={"Authorization": f"Bearer {key}"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["worker_id"]


def _row(model="poll-model", url="/v1/chat/completions", **body_extra):
    body = {"model": model}
    if url == "/v1/completions":
        body["prompt"] = "The capital of France is"
    else:
        body["messages"] = [{"role": "user", "content": "hi"}]
    body.update(body_extra)
    return json.dumps({"custom_id": "r1", "method": "POST", "url": url, "body": body}) + "\n"


def _submit(auth_client, line, endpoint="/v1/chat/completions"):
    up = auth_client.post("/v1/files", files={"file": ("in.jsonl", io.BytesIO(line.encode()), "application/jsonl")})
    assert up.status_code == 200, up.text
    cr = auth_client.post("/v1/batches", json={"input_file_id": up.json()["id"], "endpoint": endpoint})
    assert cr.status_code == 200, cr.text
    return cr.json()["id"]


def _settle(auth_client, batch_id, tries=50):
    body = None
    for _ in range(tries):
        body = auth_client.get(f"/v1/batches/{batch_id}").json()
        if body["status"] in {"validated", "failed"}:
            return body
        time.sleep(0.1)
    raise AssertionError(f"never settled: {body}")


def _required(engine, batch_id):
    SM = sessionmaker(bind=engine, expire_on_commit=False)
    db = SM()
    try:
        return db.get(Batch, batch_id).required_capabilities
    finally:
        db.close()


def _errors(body):
    return json.loads(body["error_details"])["data"]


def test_registration_stores_capabilities_and_version(auth_client, db_session):
    _reset(db_session)
    key = _worker_key(auth_client)
    _register(auth_client, key, hostname="cap-box",
              capabilities={"logprobs": True, "completions": False, "prompt_scoring": False})
    db_session.expire_all()
    rt = db_session.query(WorkerRuntime).one()
    assert rt.capabilities == {"logprobs": True, "completions": False, "prompt_scoring": False}
    assert rt.version == "0.32.14"
    _reset(db_session)


def test_logprobs_row_fails_when_the_only_host_cannot(auth_client, db_session):
    _reset(db_session)
    key = _worker_key(auth_client)
    _register(auth_client, key, hostname="old-ollama", capabilities={"logprobs": False})
    _catalog(db_session)

    body = _settle(auth_client, _submit(auth_client, _row(logprobs=True, top_logprobs=5)))
    assert body["status"] == "failed"
    assert [e["code"] for e in _errors(body)] == ["unsupported_capability"]
    assert _errors(body)[0]["field"] == "body.logprobs"
    _reset(db_session)


def test_logprobs_row_validates_when_a_host_can(auth_client, db_session, _engine):
    _reset(db_session)
    key = _worker_key(auth_client)
    _register(auth_client, key, hostname="new-ollama", capabilities={"logprobs": True})
    _catalog(db_session)

    batch_id = _submit(auth_client, _row(logprobs=True))
    assert _settle(auth_client, batch_id)["status"] == "validated"
    assert _required(_engine, batch_id) == ["logprobs"]
    _reset(db_session)


def test_logprobs_row_queues_when_no_host_is_online(auth_client, db_session, _engine):
    """No worker at all: nothing says the pool cannot do it, so wait."""
    _reset(db_session)
    _catalog(db_session)
    batch_id = _submit(auth_client, _row(logprobs=True))
    assert _settle(auth_client, batch_id)["status"] == "validated"
    assert _required(_engine, batch_id) == ["logprobs"]
    _reset(db_session)


def test_plain_chat_row_needs_nothing(auth_client, db_session, _engine):
    _reset(db_session)
    key = _worker_key(auth_client)
    _register(auth_client, key, hostname="any-box", capabilities={})
    _catalog(db_session)
    batch_id = _submit(auth_client, _row())
    assert _settle(auth_client, batch_id)["status"] == "validated"
    assert _required(_engine, batch_id) is None
    _reset(db_session)


def test_scheduler_offers_only_to_a_capable_runtime(auth_client, db_session):
    """Two hosts for the model; the logprobs batch goes to the one that can."""
    _reset(db_session)
    key = _worker_key(auth_client)
    weak = _register(auth_client, key, hostname="weak", capabilities={"logprobs": False})
    strong = _register(auth_client, key, hostname="strong", capabilities={"logprobs": True})
    _catalog(db_session)
    worker_auth = {"Authorization": f"Bearer {key}"}

    batch_id = _submit(auth_client, _row(logprobs=True))
    assert _settle(auth_client, batch_id)["status"] == "validated"

    assert auth_client.post("/workers/poll", json={"worker_id": weak}, headers=worker_auth).json()["job"] is None
    got = auth_client.post("/workers/poll", json={"worker_id": strong}, headers=worker_auth).json()["job"]
    assert got and got["job_id"] == batch_id
    _reset(db_session)


def test_completions_with_echo_needs_prompt_scoring(auth_client, db_session, _engine):
    _reset(db_session)
    key = _worker_key(auth_client)
    _register(auth_client, key, hostname="vllm-like",
              capabilities={"logprobs": True, "completions": True, "prompt_scoring": True})
    _catalog(db_session)

    line = _row(url="/v1/completions", echo=True, logprobs=1, max_tokens=0)
    batch_id = _submit(auth_client, line, endpoint="/v1/completions")
    assert _settle(auth_client, batch_id)["status"] == "validated"
    assert _required(_engine, batch_id) == ["logprobs", "completions", "prompt_scoring"]
    _reset(db_session)


def test_completions_refused_on_a_chat_only_host(auth_client, db_session):
    _reset(db_session)
    key = _worker_key(auth_client)
    _register(auth_client, key, hostname="ollama", capabilities={"logprobs": True, "completions": False})
    _catalog(db_session)

    body = _settle(auth_client, _submit(auth_client, _row(url="/v1/completions"), endpoint="/v1/completions"))
    assert body["status"] == "failed"
    assert _errors(body)[0]["code"] == "unsupported_capability"
    assert _errors(body)[0]["field"] == "url"
    _reset(db_session)


def test_completions_body_shape(auth_client, db_session):
    """Prompt, not messages; logprobs an int 0-20; echo a bool."""
    _reset(db_session)
    _catalog(db_session)
    bad = json.dumps({"custom_id": "r1", "method": "POST", "url": "/v1/completions",
                      "body": {"model": "poll-model", "logprobs": 99, "echo": "yes"}}) + "\n"
    body = _settle(auth_client, _submit(auth_client, bad, endpoint="/v1/completions"))
    assert body["status"] == "failed"
    assert sorted(e["field"] for e in _errors(body)) == ["body.echo", "body.logprobs", "body.prompt"]
    _reset(db_session)
