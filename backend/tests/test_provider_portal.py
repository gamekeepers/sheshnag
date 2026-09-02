"""Provider-portal org endpoints (spec §15 expanded design)."""

import pytest
from sqlalchemy.orm import sessionmaker

from models import (
    Organization, OrganizationMembership, Worker, Batch, BatchAssignment,
    unix_now,
)


def _seed(engine, user_id, role="owner", worker_status="online", with_batch=True):
    """Org + membership for user, one worker, optionally one served batch."""
    SM = sessionmaker(bind=engine, expire_on_commit=False)
    db = SM()
    try:
        org = Organization(name="Provider Test Org")
        db.add(org)
        db.flush()
        db.add(OrganizationMembership(org_id=org.id, user_id=user_id, role=role))

        worker = Worker(org_id=org.id, hostname="prov-worker-1", status=worker_status)
        db.add(worker)
        db.flush()

        batch = None
        if with_batch:
            batch = Batch(
                endpoint="/v1/chat/completions",
                model="llama3:8b",
                input_file_id="file-secret-input",
                status="completed",
                request_counts_total=10,
                request_counts_completed=9,
                request_counts_failed=1,
            )
            db.add(batch)
            db.flush()
            db.add(BatchAssignment(
                batch_id=batch.id,
                worker_id=worker.id,
                org_id=org.id,
                worker_hostname=worker.hostname,
                assigned_at=unix_now(),
            ))

        db.commit()
        return org, worker, batch
    finally:
        db.close()


def test_served_batches_metadata_only(auth_client, _engine, _test_user):
    org, worker, batch = _seed(_engine, _test_user.id)

    res = auth_client.get(f"/v1/orgs/{org.id}/batches")
    assert res.status_code == 200, res.text
    data = res.json()["data"]
    entry = next(e for e in data if e["id"] == batch.id)
    assert entry["worker_hostname"] == "prov-worker-1"
    assert entry["model"] == "llama3:8b"
    assert entry["request_counts"]["completed"] == 9
    # The privacy boundary: no file references in the provider view.
    assert "input_file_id" not in entry
    assert "output_file_id" not in entry


def test_stats_aggregates(auth_client, _engine, _test_user):
    org, worker, batch = _seed(_engine, _test_user.id)

    res = auth_client.get(f"/v1/orgs/{org.id}/stats?days=7")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["totals"]["jobs"] >= 1
    assert body["totals"]["requests_completed"] >= 9
    assert any(m["model"] == "llama3:8b" for m in body["by_model"])
    assert any(w["hostname"] == "prov-worker-1" for w in body["by_worker"])


def test_drain_undrain_cycle(auth_client, _engine, _test_user, db_session):
    org, worker, _ = _seed(_engine, _test_user.id, with_batch=False)

    res = auth_client.post(f"/v1/orgs/{org.id}/workers/{worker.id}/drain")
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "draining"

    res = auth_client.post(f"/v1/orgs/{org.id}/workers/{worker.id}/undrain")
    assert res.status_code == 200
    assert res.json()["status"] == "online"


def test_drain_offline_worker_409(auth_client, _engine, _test_user):
    org, worker, _ = _seed(_engine, _test_user.id, worker_status="offline", with_batch=False)
    res = auth_client.post(f"/v1/orgs/{org.id}/workers/{worker.id}/drain")
    assert res.status_code == 409


def test_remove_requires_offline(auth_client, _engine, _test_user, db_session):
    org, worker, _ = _seed(_engine, _test_user.id, with_batch=False)

    res = auth_client.delete(f"/v1/orgs/{org.id}/workers/{worker.id}")
    assert res.status_code == 409  # still online

    SM = sessionmaker(bind=_engine, expire_on_commit=False)
    db = SM()
    try:
        db.query(Worker).filter(Worker.id == worker.id).update({"status": "offline"})
        db.commit()
    finally:
        db.close()

    res = auth_client.delete(f"/v1/orgs/{org.id}/workers/{worker.id}")
    assert res.status_code == 200
    assert res.json()["deleted"] is True
    assert db_session.query(Worker).filter(Worker.id == worker.id).first() is None


def test_viewer_cannot_drain_or_rename(auth_client, _engine, _test_user):
    org, worker, _ = _seed(_engine, _test_user.id, role="viewer", with_batch=False)

    assert auth_client.post(f"/v1/orgs/{org.id}/workers/{worker.id}/drain").status_code == 403
    assert auth_client.put(f"/v1/orgs/{org.id}", json={"name": "Nope"}).status_code == 403
    # Read surfaces stay open to viewers.
    assert auth_client.get(f"/v1/orgs/{org.id}/batches").status_code == 200


def test_rename_org(auth_client, _engine, _test_user):
    org, _, _ = _seed(_engine, _test_user.id, with_batch=False)
    res = auth_client.put(f"/v1/orgs/{org.id}", json={"name": "Renamed Lab"})
    assert res.status_code == 200
    assert res.json()["name"] == "Renamed Lab"
    assert auth_client.put(f"/v1/orgs/{org.id}", json={"name": "  "}).status_code == 400


def test_history_survives_worker_removal(auth_client, _engine, _test_user):
    """Removing a worker must not erase the jobs it served.

    The provider views used to inner-join `workers` through the assignment,
    so deleting a worker silently emptied an org's contribution history —
    the one thing those views exist to show. Assignments now carry their own
    org and hostname snapshot and no FK into `workers`.
    """
    org, worker, batch = _seed(_engine, _test_user.id, worker_status="offline")

    res = auth_client.delete(f"/v1/orgs/{org.id}/workers/{worker.id}")
    assert res.status_code == 200, res.text

    data = auth_client.get(f"/v1/orgs/{org.id}/batches").json()["data"]
    entry = next((e for e in data if e["id"] == batch.id), None)
    assert entry is not None, "served batch vanished when its worker was removed"
    assert entry["worker_id"] == worker.id
    assert entry["worker_hostname"] == "prov-worker-1"   # from the snapshot
    assert entry["worker_removed"] is True

    stats = auth_client.get(f"/v1/orgs/{org.id}/stats").json()
    assert stats["totals"]["jobs"] == 1
    by_worker = {w["worker_id"]: w for w in stats["by_worker"]}
    assert by_worker[worker.id]["hostname"] == "prov-worker-1"
    assert by_worker[worker.id]["removed"] is True


# ── Token aggregation ───────────────────────────────────────────────────────
# For a provider contributing GPUs, tokens is the meaningful figure — request
# sizes vary by orders of magnitude. The trap is that rollups land only on
# completed batches, so an unguarded sum reports unfinished work as zero output.

def _org_with_workers(engine, user_id, hostnames):
    SM = sessionmaker(bind=engine, expire_on_commit=False)
    db = SM()
    try:
        org = Organization(name="Token Stats Org")
        db.add(org)
        db.flush()
        db.add(OrganizationMembership(org_id=org.id, user_id=user_id, role="owner"))
        workers = []
        for h in hostnames:
            w = Worker(org_id=org.id, hostname=h, status="online")
            db.add(w)
            db.flush()
            workers.append(w)
        db.commit()
        return org, workers
    finally:
        db.close()


def _serve(engine, org, worker, *, model, status="completed", requests=10,
           tokens=None, assigned_at=None):
    """One batch served by `worker`. `tokens` is (prompt, completion, total)."""
    SM = sessionmaker(bind=engine, expire_on_commit=False)
    db = SM()
    try:
        batch = Batch(
            endpoint="/v1/chat/completions",
            model=model,
            input_file_id="file-x",
            status=status,
            request_counts_total=requests,
            request_counts_completed=requests,
            prompt_tokens=tokens[0] if tokens else None,
            completion_tokens=tokens[1] if tokens else None,
            total_tokens=tokens[2] if tokens else None,
        )
        db.add(batch)
        db.flush()
        db.add(BatchAssignment(
            batch_id=batch.id,
            worker_id=worker.id,
            org_id=org.id,
            worker_hostname=worker.hostname,
            assigned_at=assigned_at if assigned_at is not None else unix_now(),
        ))
        db.commit()
        return batch
    finally:
        db.close()


def test_stats_sums_tokens_by_model_and_worker(auth_client, _engine, _test_user):
    org, (w1, w2) = _org_with_workers(_engine, _test_user.id, ["tok-a", "tok-b"])
    _serve(_engine, org, w1, model="gemma3:27b", tokens=(100, 40, 140))
    _serve(_engine, org, w1, model="gemma3:27b", tokens=(200, 60, 260))
    _serve(_engine, org, w2, model="qwen3:8b", tokens=(10, 5, 15))

    body = auth_client.get(f"/v1/orgs/{org.id}/stats?days=7").json()

    assert body["totals"]["total_tokens"] == 415
    assert body["totals"]["prompt_tokens"] == 310
    assert body["totals"]["counted_jobs"] == 3

    models = {m["model"]: m for m in body["by_model"]}
    assert models["gemma3:27b"]["total_tokens"] == 400
    assert models["gemma3:27b"]["counted_jobs"] == 2
    assert models["qwen3:8b"]["total_tokens"] == 15

    workers = {w["hostname"]: w for w in body["by_worker"]}
    assert workers["tok-a"]["total_tokens"] == 400
    assert workers["tok-b"]["total_tokens"] == 15


@pytest.mark.parametrize("status", ["in_progress", "failed"])
def test_stats_ignores_tokens_on_unfinished_batches(auth_client, _engine, _test_user, status):
    """A rollup on a batch that did not complete is not throughput.

    The columns are writable in any state, so the guard has to be on status —
    otherwise a requeued or failed job inflates the provider's contribution.
    """
    org, (w,) = _org_with_workers(_engine, _test_user.id, ["tok-unfinished"])
    _serve(_engine, org, w, model="m", status=status, tokens=(999, 999, 999))

    body = auth_client.get(f"/v1/orgs/{org.id}/stats?days=7").json()

    assert body["totals"]["total_tokens"] == 0
    assert body["totals"]["counted_jobs"] == 0
    assert body["totals"]["jobs"] == 1  # the job still served, it just has no count


def test_stats_distinguishes_uncounted_from_zero(auth_client, _engine, _test_user):
    """A batch predating §16 has jobs but no rollup — not a worker that idled.

    counted_jobs is what lets the UI render an em dash instead of a hard 0.
    """
    org, (w,) = _org_with_workers(_engine, _test_user.id, ["tok-uncounted"])
    _serve(_engine, org, w, model="legacy", tokens=None)

    worker = auth_client.get(f"/v1/orgs/{org.id}/stats?days=7").json()["by_worker"][0]
    assert worker["jobs"] == 1
    assert worker["counted_jobs"] == 0
    assert worker["total_tokens"] == 0


def test_stats_ranks_by_tokens_not_requests(auth_client, _engine, _test_user):
    """Many tiny requests must not outrank fewer large ones."""
    org, (w1, w2) = _org_with_workers(_engine, _test_user.id, ["tok-small", "tok-big"])
    _serve(_engine, org, w1, model="small", requests=500, tokens=(50, 50, 100))
    _serve(_engine, org, w2, model="big", requests=5, tokens=(5000, 5000, 10000))

    body = auth_client.get(f"/v1/orgs/{org.id}/stats?days=7").json()
    assert body["by_model"][0]["model"] == "big"
    assert body["by_worker"][0]["hostname"] == "tok-big"


# ── Windowing on the served-batches list ────────────────────────────────────

def test_served_batches_since_bounds_the_window(auth_client, _engine, _test_user):
    org, (w,) = _org_with_workers(_engine, _test_user.id, ["tok-window"])
    now = unix_now()
    old = _serve(_engine, org, w, model="m", assigned_at=now - 30 * 86400)
    recent = _serve(_engine, org, w, model="m", assigned_at=now - 86400)

    ids = {b["id"] for b in
           auth_client.get(f"/v1/orgs/{org.id}/batches?since={now - 7 * 86400}").json()["data"]}
    assert recent.id in ids
    assert old.id not in ids


def test_served_batches_flags_truncation(auth_client, _engine, _test_user):
    """Silence here is what turns a capped list into a chart that understates.

    Rows come back newest-first, so hitting the cap drops the oldest — the
    caller has to be told rather than left to render the gap as zero.
    """
    org, (w,) = _org_with_workers(_engine, _test_user.id, ["tok-trunc"])
    for _ in range(3):
        _serve(_engine, org, w, model="m")

    assert auth_client.get(f"/v1/orgs/{org.id}/batches?limit=2").json()["truncated"] is True
    assert auth_client.get(f"/v1/orgs/{org.id}/batches?limit=50").json()["truncated"] is False
