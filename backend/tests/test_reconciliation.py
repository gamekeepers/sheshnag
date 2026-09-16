"""Reconciliation loop (#116): worker inventory vs catalogue pins.

Rows created here use `zztest-` slugs / `zzrecon` hostnames and the catalogue
rows are removed afterwards (session-scoped test DB, no truncation).
"""
import pytest
from sqlalchemy.orm import sessionmaker

from models import ModelCatalog, RuntimeModel, ServingProfile, Worker, WorkerRuntime
from reconciliation import AVAILABLE, DRIFT, MISSING, UNREGISTERED, classify

H_KNOWN = "1" * 64
H_UNKNOWN = "2" * 64
H_DRIFTED = "3" * 64
H_ADOPT = "4" * 64


@pytest.fixture
def db(_engine):
    session = sessionmaker(bind=_engine)()
    yield session
    session.rollback()
    ids = [r.id for r in session.query(ModelCatalog).filter(ModelCatalog.id.like("zztest%"))]
    if ids:
        # Worker rows pointing at these entries: FK is ON DELETE SET NULL.
        session.query(ModelCatalog).filter(ModelCatalog.id.in_(ids)).delete(
            synchronize_session=False
        )
        session.commit()
    session.close()


def _entry(db, entry_id, rmid, digest):
    e = ModelCatalog(
        id=entry_id, display_name=entry_id, runtime="ollama", runtime_model_id=rmid,
        digest=digest, vram_gb=1.0, enabled=True, status="active",
    )
    db.add(e)
    db.flush()
    db.add(ServingProfile(catalog_id=entry_id, runtime="ollama", runtime_model_id=rmid))
    db.commit()
    return e


def _worker_key(auth_client, org_name):
    org = auth_client.post("/v1/me/organizations", json={"name": org_name}).json()
    key = auth_client.post(
        f"/v1/orgs/{org['id']}/api-keys", json={"name": "k", "key_type": "worker"}
    ).json()
    return key["api_key"]


def _register(auth_client, key, hostname, inventory):
    resp = auth_client.post(
        "/workers/register",
        json={
            "hostname": hostname,
            "runtimes": [{"type": "ollama", "endpoint": "localhost",
                          "models": [], "inventory": inventory}],
        },
        headers={"Authorization": f"Bearer {key}"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["worker_id"]


def _heartbeat(auth_client, key, worker_id, inventory, loaded=()):
    resp = auth_client.post(
        f"/workers/{worker_id}/heartbeat",
        json={"worker_id": worker_id, "activity": "idle",
              "loaded_models": list(loaded), "inventory": inventory},
        headers={"Authorization": f"Bearer {key}"},
    )
    assert resp.status_code == 200, resp.text


def _rows(db, worker_id):
    db.expire_all()
    return {
        m.name: m
        for rt in db.query(WorkerRuntime).filter_by(worker_id=worker_id)
        for m in db.query(RuntimeModel).filter_by(runtime_id=rt.id)
    }


def _item(name, sha, loaded=False, runtime="ollama"):
    return {"local_name": name, "sha256": sha, "size_bytes": 1, "loaded": loaded, "runtime": runtime}


# ─── classify() ──────────────────────────────────────────────

def test_classify_hash_first_then_name(db):
    _entry(db, "zztest-pinned-q4km", "pinned:4b", H_KNOWN)
    _entry(db, "zztest-unpinned-q4km", "unpinned:4b", None)

    # Hash match wins regardless of the name the box uses.
    assert classify(db, "whatever:latest", H_KNOWN) == (AVAILABLE, "zztest-pinned-q4km")
    # Unknown hash + unknown name -> quarantine.
    assert classify(db, "nobody:1b", H_UNKNOWN) == (UNREGISTERED, None)
    # Name claims a pinned entry, bytes differ -> drift.
    assert classify(db, "pinned:4b", H_DRIFTED) == (DRIFT, None)
    # No hash at all (old daemon, vLLM): never quarantined — name match,
    # unverified (no catalog_id), even for a name the catalogue lacks.
    assert classify(db, "pinned:4b", None) == (AVAILABLE, None)
    assert classify(db, "nobody:1b", None) == (AVAILABLE, None)
    # Unpinned entry keeps name matching even with a hash reported.
    assert classify(db, "unpinned:4b", H_DRIFTED) == (AVAILABLE, None)


# ─── register / heartbeat ────────────────────────────────────

def test_unknown_hash_is_quarantined_and_not_advertised(auth_client, db):
    _entry(db, "zztest-known-q4km", "known:4b", H_KNOWN)
    key = _worker_key(auth_client, "Recon Org Q")
    wid = _register(auth_client, key, "zzrecon-q", [
        _item("known:4b", H_KNOWN), _item("mystery:7b", H_UNKNOWN),
    ])
    rows = _rows(db, wid)
    assert rows["known:4b"].status == AVAILABLE
    assert rows["known:4b"].catalog_id == "zztest-known-q4km"
    assert rows["mystery:7b"].status == UNREGISTERED
    assert rows["mystery:7b"].catalog_id is None

    worker = db.get(Worker, wid)
    advertised = {name for name, _ in worker.advertised_models()}
    assert advertised == {"known:4b"}          # quarantined row never reaches the picker


def test_drift_is_flagged_and_excluded(auth_client, db):
    _entry(db, "zztest-drift-q4km", "driftme:4b", H_KNOWN)
    key = _worker_key(auth_client, "Recon Org D")
    wid = _register(auth_client, key, "zzrecon-d", [_item("driftme:4b", H_DRIFTED)])
    rows = _rows(db, wid)
    assert rows["driftme:4b"].status == DRIFT
    worker = db.get(Worker, wid)
    assert worker.advertised_models() == []


def test_heartbeat_marks_removed_models_missing_and_recovers(auth_client, db):
    _entry(db, "zztest-hb-q4km", "hb:4b", H_KNOWN)
    key = _worker_key(auth_client, "Recon Org M")
    wid = _register(auth_client, key, "zzrecon-m", [
        _item("hb:4b", H_KNOWN), _item("extra:1b", H_UNKNOWN),
    ])
    # `ollama rm extra:1b` on the box -> next full inventory lacks it.
    _heartbeat(auth_client, key, wid, [_item("hb:4b", H_KNOWN, loaded=True)], loaded=["hb:4b"])
    rows = _rows(db, wid)
    assert rows["extra:1b"].status == MISSING
    assert rows["hb:4b"].status == AVAILABLE and rows["hb:4b"].loaded is True
    # Empty inventory (runtime unreachable) must not mark everything missing.
    _heartbeat(auth_client, key, wid, [])
    assert _rows(db, wid)["hb:4b"].status == AVAILABLE
    # Pulled again -> back to quarantine (not trusted just because it returned).
    _heartbeat(auth_client, key, wid, [_item("hb:4b", H_KNOWN), _item("extra:1b", H_UNKNOWN)])
    assert _rows(db, wid)["extra:1b"].status == UNREGISTERED


# ─── admin: quarantine + adopt ───────────────────────────────

def test_quarantine_listing_and_adopt_flow(auth_client, superadmin_client, db):
    key = _worker_key(auth_client, "Recon Org A")
    wid = _register(auth_client, key, "zzrecon-a", [_item("newthing:8b", H_ADOPT)])
    assert _rows(db, wid)["newthing:8b"].status == UNREGISTERED

    q = superadmin_client.get("/v1/models/quarantine")
    assert q.status_code == 200, q.text
    group = next(g for g in q.json()["data"] if g["sha256"] == H_ADOPT)
    assert group["status"] == UNREGISTERED
    assert group["local_names"] == ["newthing:8b"]
    assert group["workers"][0]["worker_id"] == wid

    # Non-admins may not adopt.
    body = {
        "sha256": H_ADOPT, "id": "zztest-newthing-8b-q4km", "display_name": "New Thing 8B",
        "runtime": "ollama", "runtime_model_id": "newthing:8b", "vram_gb": 6.0,
        "quantization": "Q4_K_M", "capabilities": {"json_mode": True},
    }
    assert auth_client.post("/v1/models/adopt", json=body).status_code == 403
    # Naming rules still apply to adopted slugs.
    bad = dict(body, id="zztest-newthing-ollama")
    assert superadmin_client.post("/v1/models/adopt", json=bad).status_code == 400

    resp = superadmin_client.post("/v1/models/adopt", json=body)
    assert resp.status_code == 201, resp.text
    assert resp.json()["workers_verified"] == 1

    # The worker row flipped to available + linked; the entry is selectable.
    row = _rows(db, wid)["newthing:8b"]
    assert row.status == AVAILABLE and row.catalog_id == "zztest-newthing-8b-q4km"
    listed = {m["id"] for m in auth_client.get("/v1/models").json()["data"]}
    assert "zztest-newthing-8b-q4km" in listed
    # Same hash cannot be adopted twice.
    dup = dict(body, id="zztest-newthing-again-q4km")
    assert superadmin_client.post("/v1/models/adopt", json=dup).status_code == 409
    # Quarantine is empty for that hash now.
    assert all(g["sha256"] != H_ADOPT for g in superadmin_client.get("/v1/models/quarantine").json()["data"])


# ─── multi-runtime workers ───────────────────────────────────

def test_missing_is_scoped_to_reported_runtimes(auth_client, db):
    """A daemon inventories only its own runtime. A worker registered with
    two runtimes must not have the other runtime's rows marked missing on
    every beat (silently dead capacity, invisible to quarantine)."""
    _entry(db, "zztest-mr-q4km", "mr:4b", H_KNOWN)
    key = _worker_key(auth_client, "Recon Org MR")
    resp = auth_client.post(
        "/workers/register",
        json={"hostname": "zzrecon-mr", "runtimes": [
            {"type": "ollama", "endpoint": "localhost", "models": ["mr:4b"]},
            {"type": "vllm", "endpoint": "localhost:8000", "models": ["Org/Served-Model"]},
        ]},
        headers={"Authorization": f"Bearer {key}"},
    )
    assert resp.status_code == 200, resp.text
    wid = resp.json()["worker_id"]

    # Ollama-only report: vllm's row is untouched; a new ollama item lands
    # on the ollama runtime, not the first runtime by accident.
    _heartbeat(auth_client, key, wid, [
        _item("mr:4b", H_KNOWN, runtime="ollama"),
        _item("other:1b", H_UNKNOWN, runtime="ollama"),
    ])
    db.expire_all()
    runtimes = {rt.engine: rt for rt in db.query(WorkerRuntime).filter_by(worker_id=wid)}
    vllm_rows = {m.name: m for m in runtimes["vllm"].models}
    ollama_rows = {m.name: m for m in runtimes["ollama"].models}
    assert vllm_rows["Org/Served-Model"].status == AVAILABLE
    assert ollama_rows["mr:4b"].status == AVAILABLE
    assert ollama_rows["other:1b"].status == UNREGISTERED
    assert "other:1b" not in vllm_rows

    # Now the ollama model really is gone from an ollama report -> missing;
    # vllm still untouched.
    _heartbeat(auth_client, key, wid, [_item("mr:4b", H_KNOWN, runtime="ollama")])
    db.expire_all()
    runtimes = {rt.engine: rt for rt in db.query(WorkerRuntime).filter_by(worker_id=wid)}
    assert {m.name: m.status for m in runtimes["ollama"].models}["other:1b"] == MISSING
    assert {m.name: m.status for m in runtimes["vllm"].models}["Org/Served-Model"] == AVAILABLE


# ─── adopt validation ────────────────────────────────────────

def test_adopt_rejects_unreported_name_and_unknown_org(auth_client, superadmin_client, db):
    key = _worker_key(auth_client, "Recon Org V")
    wid = _register(auth_client, key, "zzrecon-v", [_item("realname:3b", "5" * 64)])
    base = {
        "sha256": "5" * 64, "id": "zztest-realname-3b-q4km", "display_name": "Real",
        "runtime": "ollama", "runtime_model_id": "realname:3b", "vram_gb": 3.0,
        "quantization": "Q4_K_M",
    }
    # A runtime id no worker reports would flip the row yet never dispatch.
    r = superadmin_client.post("/v1/models/adopt", json=dict(base, runtime_model_id="othername:3b"))
    assert r.status_code == 400 and "realname:3b" in r.json()["detail"]
    # Unknown org -> 400, not an FK 500 at commit.
    r = superadmin_client.post("/v1/models/adopt", json=dict(base, org_id="org-doesnotexist"))
    assert r.status_code == 400
    # Valid adopt still works and the entry advertises its status.
    r = superadmin_client.post("/v1/models/adopt", json=base)
    assert r.status_code == 201, r.text
    entry = next(m for m in auth_client.get("/v1/models").json()["data"]
                 if m["id"] == "zztest-realname-3b-q4km")
    assert entry["status"] == "unverified"
    assert _rows(db, wid)["realname:3b"].status == AVAILABLE
