"""Provider policy (#163): an availability window and a batch-size limit.

Both are hard constraints, so they live in `can_serve`: a batch that breaks
one is never offered to that worker, and pool capacity narrows with dispatch.
A batch no online worker will take because of its size fails at validation
rather than waiting in `validated` for an offer that cannot come.
"""

import io
import json
from datetime import datetime, timezone
from types import SimpleNamespace

from models import Batch, Worker
from routers import pool
from scheduler import accepts_size, in_window
from tests.test_capability_gate import _errors, _register, _settle
from tests.test_pool_capacity import _catalog_entry, _org_id, _worker as _pool_worker
from tests.test_provider_portal import _seed
from tests.test_worker_poll import _catalog, _poll, _reset, _worker_key


def _at(hh, mm):
    return datetime(2026, 10, 12, hh, mm, tzinfo=timezone.utc).timestamp()


def _policy(start=None, end=None, tz="UTC", limit=None):
    return SimpleNamespace(window_start_min=start, window_end_min=end,
                           window_tz=tz, max_batch_prompts=limit)


def _window_around_now(inside):
    """A one-hour-either-side UTC window that does or does not hold now."""
    now = datetime.now(timezone.utc)
    m = now.hour * 60 + now.minute
    if inside:
        return (m - 60) % 1440, (m + 60) % 1440
    return (m + 60) % 1440, (m + 120) % 1440


def _set_policy(db, worker_id, **fields):
    worker = db.get(Worker, worker_id)
    for name, value in fields.items():
        setattr(worker, name, value)
    db.commit()


# ─── The predicates ─────────────────────────────────────────

def test_no_window_is_always_open():
    assert in_window(_policy(), at=_at(3, 0))


def test_daytime_window_includes_start_and_excludes_end():
    day = _policy(9 * 60, 17 * 60)
    assert in_window(day, at=_at(9, 0))
    assert in_window(day, at=_at(16, 59))
    assert not in_window(day, at=_at(17, 0))
    assert not in_window(day, at=_at(8, 59))


def test_overnight_window_wraps_past_midnight():
    night = _policy(18 * 60, 8 * 60)
    assert in_window(night, at=_at(23, 0))
    assert in_window(night, at=_at(0, 30))
    assert in_window(night, at=_at(7, 59))
    assert not in_window(night, at=_at(8, 0))
    assert not in_window(night, at=_at(12, 0))


def test_window_is_read_in_the_providers_timezone():
    """09:00-10:00 in Kolkata (UTC+5:30) is 03:30-04:30 UTC."""
    kolkata = _policy(9 * 60, 10 * 60, tz="Asia/Kolkata")
    assert in_window(kolkata, at=_at(3, 45))
    assert not in_window(kolkata, at=_at(9, 30))


def test_size_limit():
    assert accepts_size(_policy(limit=500), 500)
    assert not accepts_size(_policy(limit=500), 501)
    assert accepts_size(_policy(), 10_000)
    assert accepts_size(_policy(limit=500), None)  # capacity asks without a size


# ─── Dispatch ───────────────────────────────────────────────

def _queued(db, batch_id, prompts):
    db.add(Batch(id=batch_id, endpoint="/v1/chat/completions", input_file_id="f-policy",
                 status="validated", model="poll-model", request_counts_total=prompts))
    db.commit()


def test_a_batch_over_the_limit_is_never_offered(auth_client, db_session):
    _reset(db_session)
    key = _worker_key(auth_client)
    worker_id = _register(auth_client, key, hostname="capped", capabilities={})
    _catalog(db_session)
    _set_policy(db_session, worker_id, max_batch_prompts=500)

    _queued(db_session, "batch-big", 1000)
    assert _poll(auth_client, key, worker_id).json()["job"] is None

    _queued(db_session, "batch-small", 400)
    assert _poll(auth_client, key, worker_id).json()["job"]["job_id"] == "batch-small"
    _reset(db_session)


def test_a_worker_outside_its_window_gets_nothing(auth_client, db_session):
    _reset(db_session)
    key = _worker_key(auth_client)
    worker_id = _register(auth_client, key, hostname="night-only", capabilities={})
    _catalog(db_session)
    _queued(db_session, "batch-window", 10)

    start, end = _window_around_now(inside=False)
    _set_policy(db_session, worker_id, window_start_min=start, window_end_min=end, window_tz="UTC")
    assert _poll(auth_client, key, worker_id).json()["job"] is None

    start, end = _window_around_now(inside=True)
    _set_policy(db_session, worker_id, window_start_min=start, window_end_min=end)
    assert _poll(auth_client, key, worker_id).json()["job"]["job_id"] == "batch-window"
    _reset(db_session)


# ─── Validation: a batch nobody online will take ────────────

def _submit_rows(auth_client, n):
    lines = "".join(
        json.dumps({"custom_id": f"r{i}", "method": "POST", "url": "/v1/chat/completions",
                    "body": {"model": "poll-model", "messages": [{"role": "user", "content": "hi"}]}})
        + "\n"
        for i in range(n)
    )
    up = auth_client.post("/v1/files", files={"file": ("in.jsonl", io.BytesIO(lines.encode()), "application/jsonl")})
    assert up.status_code == 200, up.text
    cr = auth_client.post("/v1/batches", json={"input_file_id": up.json()["id"], "endpoint": "/v1/chat/completions"})
    assert cr.status_code == 200, cr.text
    return cr.json()["id"]


def test_too_large_for_every_online_host_fails_at_validation(auth_client, db_session):
    _reset(db_session)
    key = _worker_key(auth_client)
    a = _register(auth_client, key, hostname="cap-2", capabilities={})
    b = _register(auth_client, key, hostname="cap-1", capabilities={})
    _catalog(db_session)
    _set_policy(db_session, a, max_batch_prompts=2)
    _set_policy(db_session, b, max_batch_prompts=1)

    body = _settle(auth_client, _submit_rows(auth_client, 3))
    assert body["status"] == "failed"
    err = _errors(body)[0]
    assert err["code"] == "batch_too_large"
    assert "more than 2 requests" in err["message"]
    _reset(db_session)


def test_one_host_without_a_limit_is_enough(auth_client, db_session):
    _reset(db_session)
    key = _worker_key(auth_client)
    capped = _register(auth_client, key, hostname="capped", capabilities={})
    _register(auth_client, key, hostname="open", capabilities={})
    _catalog(db_session)
    _set_policy(db_session, capped, max_batch_prompts=1)

    assert _settle(auth_client, _submit_rows(auth_client, 3))["status"] == "validated"
    _reset(db_session)


def test_a_host_outside_its_window_still_counts(auth_client, db_session):
    """Off-hours is unavailable now, not unable: the window reopens."""
    _reset(db_session)
    key = _worker_key(auth_client)
    capped = _register(auth_client, key, hostname="capped", capabilities={})
    night = _register(auth_client, key, hostname="night", capabilities={})
    _catalog(db_session)
    _set_policy(db_session, capped, max_batch_prompts=1)
    start, end = _window_around_now(inside=False)
    _set_policy(db_session, night, window_start_min=start, window_end_min=end, window_tz="UTC")

    assert _settle(auth_client, _submit_rows(auth_client, 3))["status"] == "validated"
    _reset(db_session)


def test_no_host_online_queues(auth_client, db_session):
    _reset(db_session)
    _catalog(db_session)
    assert _settle(auth_client, _submit_rows(auth_client, 3))["status"] == "validated"
    _reset(db_session)


# ─── Advertised capacity ────────────────────────────────────

def test_capacity_drops_a_worker_outside_its_window(auth_client, db_session):
    _reset(db_session)
    pool._reset_cache()
    org = _org_id(auth_client, "Policy Pool Org")
    _catalog_entry(db_session, entry_id="policy-model")
    worker = _pool_worker(db_session, org, "night-box", models=("policy-model:latest",))

    start, end = _window_around_now(inside=False)
    _set_policy(db_session, worker.id, window_start_min=start, window_end_min=end, window_tz="UTC")
    servable = [m["id"] for m in auth_client.get("/v1/pool/capacity").json()["models_servable"]]
    assert "policy-model" not in servable

    pool._reset_cache()
    start, end = _window_around_now(inside=True)
    _set_policy(db_session, worker.id, window_start_min=start, window_end_min=end)
    servable = [m["id"] for m in auth_client.get("/v1/pool/capacity").json()["models_servable"]]
    assert "policy-model" in servable
    pool._reset_cache()
    _reset(db_session)


# ─── PATCH /orgs/{org_id}/workers/{worker_id}/policy ────────

def _patch(auth_client, org, worker, body):
    return auth_client.patch(f"/v1/orgs/{org.id}/workers/{worker.id}/policy", json=body)


def test_set_read_and_clear_policy(auth_client, _engine, _test_user):
    org, worker, _ = _seed(_engine, _test_user.id, with_batch=False)

    res = _patch(auth_client, org, worker, {
        "window_start": "18:00", "window_end": "08:00",
        "window_tz": "Asia/Kolkata", "max_batch_prompts": 500,
    })
    assert res.status_code == 200, res.text
    assert res.json()["policy"] == {
        "window_start": "18:00", "window_end": "08:00",
        "window_tz": "Asia/Kolkata", "max_batch_prompts": 500,
    }

    # Only the fields sent change.
    res = _patch(auth_client, org, worker, {"max_batch_prompts": None})
    assert res.json()["policy"]["window_start"] == "18:00"
    assert res.json()["policy"]["max_batch_prompts"] is None

    listed = auth_client.get(f"/v1/orgs/{org.id}/workers").json()["data"]
    assert next(w for w in listed if w["id"] == worker.id)["policy"]["window_tz"] == "Asia/Kolkata"

    res = _patch(auth_client, org, worker, {"window_start": None, "window_end": None, "window_tz": None})
    assert res.json()["policy"] == {
        "window_start": None, "window_end": None, "window_tz": None, "max_batch_prompts": None,
    }


def test_policy_rejects_bad_input(auth_client, _engine, _test_user):
    org, worker, _ = _seed(_engine, _test_user.id, with_batch=False)
    full = {"window_start": "18:00", "window_end": "08:00", "window_tz": "UTC"}

    assert _patch(auth_client, org, worker, {**full, "window_start": "25:00"}).status_code == 400
    assert _patch(auth_client, org, worker, {**full, "window_tz": "Mars/Olympus"}).status_code == 400
    assert _patch(auth_client, org, worker, {**full, "window_end": "18:00"}).status_code == 400
    assert _patch(auth_client, org, worker, {"window_start": "18:00"}).status_code == 400  # half a window
    assert _patch(auth_client, org, worker, {**full, "window_start": "9:00"}).status_code == 400  # not zero-padded
    assert _patch(auth_client, org, worker, {**full, "window_end": "8:5"}).status_code == 400
    assert _patch(auth_client, org, worker, {"max_batch_prompts": 0}).status_code == 422
    assert _patch(auth_client, org, worker, {"max_batch_prompts": 50_001}).status_code == 422  # over MAX_LINES
    assert _patch(auth_client, org, worker, {"max_batch_prompts": 2**31}).status_code == 422  # would overflow INTEGER
    assert _patch(auth_client, org, worker, {"max_batch_prompts": 50_000}).status_code == 200


def test_viewer_cannot_set_policy(auth_client, _engine, _test_user):
    org, worker, _ = _seed(_engine, _test_user.id, role="viewer", with_batch=False)
    assert _patch(auth_client, org, worker, {"max_batch_prompts": 10}).status_code == 403
