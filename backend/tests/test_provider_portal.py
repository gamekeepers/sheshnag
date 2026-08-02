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
            db.add(BatchAssignment(batch_id=batch.id, worker_id=worker.id, assigned_at=unix_now()))

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
