"""Aggregate pool capacity — GET /v1/pool/capacity.

Two things this endpoint must never get wrong: it must not leak which
machine belongs to whom, and its idea of "servable" must be the
scheduler's, not a lookalike that promises a model no worker can run.
"""

import pytest

from models import ModelCatalog, Worker, WorkerGpu, WorkerRuntime, RuntimeModel, unix_now
from routers import pool


@pytest.fixture(autouse=True)
def _clean_pool(db_session):
    """Empty worker table + no cached snapshot, so counts are this test's.

    The suite shares one database for the whole session, so a worker left
    behind by another module would show up in these aggregates.
    """
    db_session.query(RuntimeModel).delete()
    db_session.query(WorkerRuntime).delete()
    db_session.query(WorkerGpu).delete()
    db_session.query(Worker).delete()
    db_session.commit()
    pool._reset_cache()
    yield
    pool._reset_cache()


@pytest.fixture
def anon_client(_engine):
    """TestClient with no Authorization header at all."""
    from fastapi.testclient import TestClient
    from database import get_db
    from main import app
    from tests.conftest import _make_override

    app.dependency_overrides[get_db] = _make_override(_engine)
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def _catalog_entry(db, entry_id="pool-test-model", vram_gb=8.0, org_id=None):
    entry = db.query(ModelCatalog).filter_by(id=entry_id).first()
    if entry is None:
        entry = ModelCatalog(
            id=entry_id,
            display_name="Pool Test Model",
            runtime="ollama",
            runtime_model_id=f"{entry_id}:latest",
            vram_gb=vram_gb,
            parameter_size="7B",
            org_id=org_id,
        )
        db.add(entry)
        db.commit()
    return entry


def _worker(db, org_id, hostname, *, vram=24.0, activity="idle",
            models=("pool-test-model:latest",), heartbeat_age=0, status="online",
            gpus=1):
    worker = Worker(
        org_id=org_id,
        hostname=hostname,
        status=status,
        activity=activity,
        vram_total_gb=vram,
        last_heartbeat=unix_now() - heartbeat_age,
    )
    db.add(worker)
    db.flush()
    for i in range(gpus):
        db.add(WorkerGpu(worker_id=worker.id, gpu_index=i, vendor="nvidia",
                         name="RTX 4090", vram_gb=vram / max(gpus, 1)))
    runtime = WorkerRuntime(worker_id=worker.id, engine="ollama", base_url="localhost")
    db.add(runtime)
    db.flush()
    for name in models:
        db.add(RuntimeModel(runtime_id=runtime.id, name=name, loaded=False))
    db.commit()
    return worker


def _org_id(auth_client, name):
    return auth_client.post("/v1/me/organizations", json={"name": name}).json()["id"]


def test_anonymous_gets_counts_and_no_identifying_detail(auth_client, anon_client, db_session):
    org = _org_id(auth_client, "Pool Org A")
    _catalog_entry(db_session)
    _worker(db_session, org, "gpu-box-01", activity="idle")
    _worker(db_session, org, "gpu-box-02", activity="busy")

    resp = anon_client.get("/v1/pool/capacity")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["workers_online"] == 2
    assert body["workers_idle"] == 1
    assert body["workers_busy"] == 1
    assert "pool-test-model" in [m["id"] for m in body["models_servable"]]

    # Nothing that names a machine or its owner, at any depth.
    blob = resp.text
    for secret in ("gpu-box-01", "gpu-box-02", org, "hostname", "worker_id", "RTX 4090"):
        assert secret not in blob


def test_hardware_figures_hidden_from_anonymous_and_on_a_thin_pool(auth_client, anon_client, db_session):
    """VRAM and GPU count travel together, and neither survives a thin pool."""
    org = _org_id(auth_client, "Pool Org B")
    _catalog_entry(db_session)
    _worker(db_session, org, "thin-1", vram=141.0, gpus=2)
    _worker(db_session, org, "thin-2", vram=141.0, gpus=2)

    # Two workers: even an authenticated caller sees no hardware figures,
    # because "282 GB across 2 machines" names whose machines they are.
    for body in (anon_client.get("/v1/pool/capacity").json(),
                 auth_client.get("/v1/pool/capacity").json()):
        assert body["vram_total_gb"] is None
        assert body["gpus_online"] is None

    pool._reset_cache()
    _worker(db_session, org, "thin-3", vram=18.0, gpus=1)

    body = auth_client.get("/v1/pool/capacity").json()
    assert body["vram_total_gb"] == 300.0
    assert body["gpus_online"] == 5

    # Still withheld from anonymous callers regardless of pool size.
    pool._reset_cache()
    anon = anon_client.get("/v1/pool/capacity").json()
    assert anon["vram_total_gb"] is None and anon["gpus_online"] is None


def test_servable_follows_the_scheduler_not_the_catalogue(auth_client, db_session):
    """A catalogue entry no online worker can fit is not 'servable'."""
    org = _org_id(auth_client, "Pool Org C")
    _catalog_entry(db_session, "pool-small", vram_gb=8.0)
    _catalog_entry(db_session, "pool-huge", vram_gb=140.0)
    _worker(db_session, org, "modest-box", vram=24.0,
            models=("pool-small:latest", "pool-huge:latest"))

    ids = [m["id"] for m in auth_client.get("/v1/pool/capacity").json()["models_servable"]]
    assert "pool-small" in ids          # hosted and fits
    assert "pool-huge" not in ids       # hosted but will not fit


def test_model_not_hosted_is_not_servable(auth_client, db_session):
    org = _org_id(auth_client, "Pool Org D")
    _catalog_entry(db_session, "pool-absent", vram_gb=1.0)
    _worker(db_session, org, "empty-box", models=())

    ids = [m["id"] for m in auth_client.get("/v1/pool/capacity").json()["models_servable"]]
    assert "pool-absent" not in ids


def test_heartbeat_silent_worker_is_not_counted_online(auth_client, db_session):
    """The sweeper flips `status` only once a minute; liveness is read-time."""
    org = _org_id(auth_client, "Pool Org E")
    _catalog_entry(db_session)
    _worker(db_session, org, "zombie-box", heartbeat_age=600)

    body = auth_client.get("/v1/pool/capacity").json()
    assert body["workers_online"] == 0
    assert body["models_servable"] == []


def test_org_private_model_hidden_from_non_member(auth_client, anon_client, db_session):
    org = _org_id(auth_client, "Pool Org F")
    other_org = _org_id(auth_client, "Pool Org G")
    _catalog_entry(db_session, "pool-private", vram_gb=4.0, org_id=other_org)
    _worker(db_session, org, "private-host", models=("pool-private:latest",))

    anon_ids = [m["id"] for m in anon_client.get("/v1/pool/capacity").json()["models_servable"]]
    assert "pool-private" not in anon_ids

    # The test user owns both orgs, so the private entry is selectable for them.
    pool._reset_cache()
    member_ids = [m["id"] for m in auth_client.get("/v1/pool/capacity").json()["models_servable"]]
    assert "pool-private" in member_ids


def test_garbage_token_degrades_to_anonymous(anon_client, auth_client, db_session):
    org = _org_id(auth_client, "Pool Org H")
    _catalog_entry(db_session)
    for i in range(3):
        _worker(db_session, org, f"tok-{i}")

    resp = anon_client.get(
        "/v1/pool/capacity", headers={"Authorization": "Bearer not-a-real-token"}
    )
    assert resp.status_code == 200
    assert resp.json()["workers_online"] == 3
    assert resp.json()["vram_total_gb"] is None
    assert resp.json()["gpus_online"] is None
