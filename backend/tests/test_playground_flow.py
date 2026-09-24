"""The playground's request sequence, end to end, against a fake worker.

app/dashboard/Playground.js has no test harness of its own, so its contract
with the backend is pinned here: a one-line file goes up, becomes a batch,
validates against the catalogue, is taken by a worker, and comes back as an
output file whose row carries the answer — with the serving worker's id
on the batch the whole way from dispatch to completion.
"""

import io
import json
import time

from tests.test_worker_poll import _catalog, _register, _reset, _worker_key


def _wait_status(client, batch_id, wanted, tries=50):
    """Validation runs as a background task on the app's loop; give it a moment."""
    body = None
    for _ in range(tries):
        body = client.get(f"/v1/batches/{batch_id}").json()
        if body["status"] in wanted:
            return body
        time.sleep(0.1)
    raise AssertionError(f"batch never reached {wanted}: {body}")


def _playground_line(model="poll-model", custom_id="playground-1"):
    return json.dumps({
        "custom_id": custom_id,
        "method": "POST",
        "url": "/v1/chat/completions",
        # Every control the playground exposes, so the validator is on record
        # accepting the full body shape, not just model + messages.
        "body": {
            "model": model,
            "messages": [{"role": "user", "content": "Say hello."}],
            "temperature": 0.7,
            "max_tokens": 512,
            "top_p": 0.9,
            "top_k": 40,
            "seed": 7,
            "response_format": {"type": "json_object"},
        },
    }) + "\n"


def test_playground_one_line_batch_round_trip(auth_client, db_session):
    _reset(db_session)
    key = _worker_key(auth_client)
    worker_id = _register(auth_client, key)
    _catalog(db_session)
    worker_auth = {"Authorization": f"Bearer {key}"}

    # 1. Upload the one-line file exactly as the browser does (multipart, field `file`).
    up = auth_client.post(
        "/v1/files",
        files={"file": ("playground-1.jsonl", io.BytesIO(_playground_line().encode()), "application/jsonl")},
    )
    assert up.status_code == 200, up.text
    file_id = up.json()["id"]

    # 2. Create the batch; nobody has taken it, so no worker is reported.
    cr = auth_client.post(
        "/v1/batches",
        json={"input_file_id": file_id, "endpoint": "/v1/chat/completions", "completion_window": "24h"},
    )
    assert cr.status_code == 200, cr.text
    batch = cr.json()
    assert batch["status"] == "validating"
    assert batch["worker_id"] is None

    # 3. Validation resolves the model from body.model.
    batch = _wait_status(auth_client, batch["id"], {"validated", "failed"})
    assert batch["status"] == "validated", batch
    assert batch["model"] == "poll-model"

    # 4. A worker polls and takes it; the batch now names the worker.
    poll = auth_client.post("/workers/poll", json={"worker_id": worker_id}, headers=worker_auth)
    assert poll.status_code == 200, poll.text
    assert poll.json()["job"]["job_id"] == batch["id"]
    running = auth_client.get(f"/v1/batches/{batch['id']}").json()
    assert running["status"] == "in_progress"
    assert running["worker_id"] == worker_id
    assert running["in_progress_at"] is not None

    # 5. The worker uploads one output row in the daemon's shape.
    out = json.dumps({
        "custom_id": "playground-1",
        "error": None,
        "response": {
            "model": "poll-model:latest",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "hello"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
        },
    }) + "\n"
    res = auth_client.post(
        "/workers/upload-results",
        data={"job_id": batch["id"], "worker_id": worker_id, "completed": 1, "failed": 0},
        files={"file": ("out.jsonl", io.BytesIO(out.encode()), "application/jsonl")},
        headers=worker_auth,
    )
    assert res.status_code == 200, res.text

    # 6. Terminal state still carries the worker, and the output row is readable.
    done = auth_client.get(f"/v1/batches/{batch['id']}").json()
    assert done["status"] == "completed"
    assert done["worker_id"] == worker_id
    assert done["output_file_id"]

    content = auth_client.get(f"/v1/files/{done['output_file_id']}/content")
    assert content.status_code == 200, content.text
    rows = [json.loads(line) for line in content.text.splitlines() if line]
    mine = next(r for r in rows if r["custom_id"] == "playground-1")
    assert mine["response"]["choices"][0]["message"]["content"] == "hello"
    assert mine["response"]["usage"]["total_tokens"] == 4
    assert mine["response"]["model"] == "poll-model:latest"

    _reset(db_session)


def test_playground_line_for_unknown_model_fails_at_validation(auth_client, db_session):
    """The picker only offers catalogue ids, but a stale tab can name one that
    has since been disabled. That must surface as a failed batch with a
    reason, not hang in the queue."""
    _reset(db_session)
    up = auth_client.post(
        "/v1/files",
        files={"file": ("playground-2.jsonl", io.BytesIO(_playground_line(model="no-such-model").encode()), "application/jsonl")},
    )
    cr = auth_client.post(
        "/v1/batches",
        json={"input_file_id": up.json()["id"], "endpoint": "/v1/chat/completions", "completion_window": "24h"},
    )
    batch = _wait_status(auth_client, cr.json()["id"], {"validated", "failed"})
    assert batch["status"] == "failed"
    assert "no-such-model" in (batch["error_details"] or "")
    assert batch["worker_id"] is None
    _reset(db_session)


def test_grid_is_a_group_of_batches_found_by_metadata(auth_client, db_session):
    """Two arms, one grid_id. Each arm is its own batch and the list is
    filterable by the tag; a worker takes them one at a time."""
    _reset(db_session)
    key = _worker_key(auth_client)
    worker_id = _register(auth_client, key)
    _catalog(db_session)
    worker_auth = {"Authorization": f"Bearer {key}"}
    grid_id = "grid-test"

    arms = []
    for i in range(2):
        line = _playground_line(custom_id=f"{grid_id}-a{i}-p0")
        up = auth_client.post("/v1/files", files={"file": (f"{grid_id}-arm{i}.jsonl", io.BytesIO(line.encode()), "application/jsonl")})
        cr = auth_client.post("/v1/batches", json={
            "input_file_id": up.json()["id"], "endpoint": "/v1/chat/completions", "completion_window": "24h",
            "metadata": {"grid_id": grid_id, "arm": str(i)},
        })
        assert cr.status_code == 200, cr.text
        arms.append(cr.json())

    for arm in arms:
        assert _wait_status(auth_client, arm["id"], {"validated", "failed"})["status"] == "validated"

    mine = [b for b in auth_client.get("/v1/batches").json()["data"] if (b["metadata"] or {}).get("grid_id") == grid_id]
    assert sorted(b["metadata"]["arm"] for b in mine) == ["0", "1"]

    # FIFO: the worker gets arm 0 first, then arm 1.
    for expected in arms:
        poll = auth_client.post("/workers/poll", json={"worker_id": worker_id}, headers=worker_auth)
        assert poll.json()["job"]["job_id"] == expected["id"]
        out = json.dumps({"custom_id": f"{grid_id}-a{expected['metadata']['arm']}-p0", "error": None,
                          "response": {"model": "poll-model:latest",
                                       "choices": [{"index": 0, "message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}],
                                       "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}}) + "\n"
        res = auth_client.post("/workers/upload-results",
                               data={"job_id": expected["id"], "worker_id": worker_id, "completed": 1, "failed": 0},
                               files={"file": ("out.jsonl", io.BytesIO(out.encode()), "application/jsonl")},
                               headers=worker_auth)
        assert res.status_code == 200, res.text

    done = {b["id"]: b for b in auth_client.get("/v1/batches").json()["data"]}
    for arm in arms:
        assert done[arm["id"]]["status"] == "completed"
        assert done[arm["id"]]["metadata"]["grid_id"] == grid_id
    _reset(db_session)
