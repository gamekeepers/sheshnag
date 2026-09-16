"""Auto-adopt (#116 step 3): quarantined hashes the identity resolver
confirms become catalogue entries on the sweeper pass, with no human.

Catalogue rows use the `zzauto` prefix and are removed afterwards; the
resolver is stubbed — its own behaviour is covered in test_identity_resolver.
"""
import pytest
from sqlalchemy.orm import sessionmaker

import catalog_service
from catalog_service import (
    auto_adopt_pass, draft_slug, estimate_vram_gb, infer_task_and_capabilities,
    run_auto_adopt_once,
)
from identity_resolver import Confirmed, Unconfirmed
from models import CatalogArtifactFile, ModelCatalog, RuntimeModel, WorkerRuntime
from reconciliation import AVAILABLE, UNREGISTERED

H1 = "a1" * 32
H2 = "b2" * 32
SIZE = 2497280256
DETAILS = {"quantization": "Q4_K_M", "parameter_size": "4.0B",
           "family": "qwen3", "context_length": 40960}


class StubResolver:
    def __init__(self, confirmed=None):
        self.confirmed = confirmed or {}
        self.calls = []

    def resolve(self, name, sha256, **hints):
        self.calls.append((name, sha256, hints))
        return self.confirmed.get((name, sha256), Unconfirmed("digest-mismatch"))


def _confirmed(name, digest):
    return Confirmed(source_type="ollama-library", source_ref=f"library/{name.split(':')[0]}",
                     source_revision=None, digest=digest,
                     homepage_url=f"https://ollama.com/library/{name.split(':')[0]}")


@pytest.fixture
def db(_engine):
    session = sessionmaker(bind=_engine)()
    yield session
    session.rollback()
    ids = [r.id for r in session.query(ModelCatalog).filter(ModelCatalog.id.like("%zzauto%"))]
    if ids:
        session.query(ModelCatalog).filter(ModelCatalog.id.in_(ids)).delete(synchronize_session=False)
        session.commit()
    session.close()


def _worker_key(auth_client, org_name):
    org = auth_client.post("/v1/me/organizations", json={"name": org_name}).json()
    key = auth_client.post(f"/v1/orgs/{org['id']}/api-keys",
                           json={"name": "k", "key_type": "worker"}).json()
    return key["api_key"]


def _register(auth_client, key, hostname, inventory):
    resp = auth_client.post(
        "/workers/register",
        json={"hostname": hostname,
              "runtimes": [{"type": "ollama", "endpoint": "localhost", "models": [],
                            "inventory": inventory}]},
        headers={"Authorization": f"Bearer {key}"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["worker_id"]


def _item(name, sha, details=DETAILS, size=SIZE):
    return {"local_name": name, "sha256": sha, "size_bytes": size, "loaded": False,
            "runtime": "ollama", "details": details}


def _row(db, worker_id, name):
    db.expire_all()
    return next(
        m for rt in db.query(WorkerRuntime).filter_by(worker_id=worker_id)
        for m in db.query(RuntimeModel).filter_by(runtime_id=rt.id) if m.name == name
    )


# ─── pure helpers ────────────────────────────────────────────

def test_slug_vram_and_capability_helpers():
    assert draft_slug("qwen3:4b", "Q4_K_M") == "qwen3-4b-q4km"
    assert draft_slug("hf.co/unsloth/Qwen3-GGUF:Q4_K_M", "Q4_K_M") == "hf-co-unsloth-qwen3-gguf-q4km"
    assert draft_slug("nomic-embed-text:latest", "F16") == "nomic-embed-text-latest-f16"
    assert draft_slug("plain:1b", None) == "plain-1b"
    assert draft_slug("registry-user/model:Q8_0", "Q8_0") == "registry-user-model-q80"
    assert estimate_vram_gb(SIZE) == 3.3          # 2.33 GiB * 1.2 + 0.5
    assert estimate_vram_gb(None) is None
    assert infer_task_and_capabilities("nomic-embed-text:latest", {"family": "nomic-bert"}) == (
        "embedding", {"embeddings": True, "json_mode": False, "vision": False})
    assert infer_task_and_capabilities("gemma3:4b", {"family": "gemma3"})[1]["vision"] is True
    assert infer_task_and_capabilities("qwen2.5vl:3b", {"family": "qwen25vl"})[1]["vision"] is True
    assert infer_task_and_capabilities("qwen3:4b", {"family": "qwen3"}) == (
        "chat", {"json_mode": True, "vision": False, "embeddings": False})


# ─── the pass ────────────────────────────────────────────────

def test_confirmed_hash_is_adopted_and_rows_flip(auth_client, db):
    key = _worker_key(auth_client, "Auto Org 1")
    wid = _register(auth_client, key, "zzauto-box1", [_item("zzauto-model:4b", H1)])
    assert _row(db, wid, "zzauto-model:4b").status == UNREGISTERED

    resolver = StubResolver({("zzauto-model:4b", H1): _confirmed("zzauto-model:4b", H1)})
    assert auto_adopt_pass(db, resolver, enabled=True) == 1

    entry = db.query(ModelCatalog).filter_by(id="zzauto-model-4b-q4km").one()
    assert entry.status == "unverified" and entry.enabled is True
    assert entry.adopted_by == "auto"
    assert entry.digest == H1
    assert (entry.source_type, entry.source_ref) == ("ollama-library", "library/zzauto-model")
    assert entry.homepage_url == "https://ollama.com/library/zzauto-model"
    assert entry.quantization == "Q4_K_M" and entry.parameter_size == "4.0B"
    assert entry.context_length == 40960 and entry.size_gb == 2.33
    assert entry.vram_gb == 3.3
    assert entry.task_type == "chat" and entry.capabilities["json_mode"] is True
    assert [(p.runtime, p.runtime_model_id) for p in entry.profiles] == [("ollama", "zzauto-model:4b")]

    row = _row(db, wid, "zzauto-model:4b")
    assert row.status == AVAILABLE and row.catalog_id == entry.id

    # Idempotent: nothing left to adopt, resolver not even consulted.
    resolver.calls.clear()
    assert auto_adopt_pass(db, resolver, enabled=True) == 0
    assert resolver.calls == []

    listed = {m["id"]: m for m in auth_client.get("/v1/models").json()["data"]}
    assert listed["zzauto-model-4b-q4km"]["adopted_by"] == "auto"
    assert listed["zzauto-model-4b-q4km"]["status"] == "unverified"


def test_unconfirmed_hash_stays_quarantined(auth_client, db):
    key = _worker_key(auth_client, "Auto Org 2")
    wid = _register(auth_client, key, "zzauto-box2", [_item("zzauto-private:7b", H2)])
    assert auto_adopt_pass(db, StubResolver(), enabled=True) == 0
    assert _row(db, wid, "zzauto-private:7b").status == UNREGISTERED
    assert db.query(ModelCatalog).filter(ModelCatalog.digest == H2).first() is None


def test_slug_collision_gets_numeric_suffix(auth_client, db):
    db.add(ModelCatalog(id="zzauto-taken-1b-q40", display_name="taken", runtime="ollama",
                        runtime_model_id="elsewhere:1b", digest="c3" * 32, enabled=True))
    db.commit()
    key = _worker_key(auth_client, "Auto Org 3")
    _register(auth_client, key, "zzauto-box3",
              [_item("zzauto-taken:1b", "d4" * 32, details=dict(DETAILS, quantization="Q4_0"))])
    resolver = StubResolver({("zzauto-taken:1b", "d4" * 32): _confirmed("zzauto-taken:1b", "d4" * 32)})
    assert auto_adopt_pass(db, resolver, enabled=True) == 1
    # Counter sits before the quant so the id still satisfies the naming rule.
    assert db.query(ModelCatalog).filter_by(id="zzauto-taken-1b-2-q40").one().digest == "d4" * 32


def test_staged_when_enabled_false(auth_client, db):
    key = _worker_key(auth_client, "Auto Org 4")
    _register(auth_client, key, "zzauto-box4", [_item("zzauto-staged:2b", "e5" * 32)])
    resolver = StubResolver({("zzauto-staged:2b", "e5" * 32): _confirmed("zzauto-staged:2b", "e5" * 32)})
    assert auto_adopt_pass(db, resolver, enabled=False) == 1
    entry = db.query(ModelCatalog).filter_by(id="zzauto-staged-2b-q4km").one()
    assert entry.enabled is False
    assert "zzauto-staged-2b-q4km" not in {m["id"] for m in auth_client.get("/v1/models").json()["data"]}


def test_mode_off_skips_pass(monkeypatch):
    monkeypatch.setenv("CATALOG_AUTO_ADOPT", "off")
    monkeypatch.setattr(catalog_service, "_resolver", None)
    assert run_auto_adopt_once() == 0
    assert catalog_service._resolver is None       # never even built


def test_admin_adopt_records_adopter(auth_client, superadmin_client, db):
    key = _worker_key(auth_client, "Auto Org 5")
    _register(auth_client, key, "zzauto-box5", [_item("zzauto-manual:3b", "f6" * 32)])
    r = superadmin_client.post("/v1/models/adopt", json={
        "sha256": "f6" * 32, "id": "zzauto-manual-3b-q4km", "display_name": "Manual",
        "runtime": "ollama", "runtime_model_id": "zzauto-manual:3b", "vram_gb": 3.0,
        "quantization": "Q4_K_M",
    })
    assert r.status_code == 201, r.text
    entry = db.query(ModelCatalog).filter_by(id="zzauto-manual-3b-q4km").one()
    assert entry.adopted_by not in (None, "auto")


def test_hf_confirmation_pins_the_matched_file(auth_client, db):
    """HF confirmation matches bytes, not the :tag — the file that matched is
    pinned in catalog_artifact_files so the reference is re-pullable even
    when the worker's tag lies about the quant."""
    key = _worker_key(auth_client, "Auto Org 6")
    name = "hf.co/zzauto/Model-GGUF:Q4_K_M"
    _register(auth_client, key, "zzauto-box6", [_item(name, "a7" * 32)])
    resolver = StubResolver({(name, "a7" * 32): Confirmed(
        source_type="huggingface", source_ref="zzauto/Model-GGUF",
        source_revision="0a1b2c3d", digest="a7" * 32,
        homepage_url="https://huggingface.co/zzauto/Model-GGUF",
        source_file="Model-IQ3_M.gguf",           # the tag said Q4_K_M; bytes are IQ3_M
    )})
    assert auto_adopt_pass(db, resolver, enabled=True) == 1
    entry = db.query(ModelCatalog).filter_by(digest="a7" * 32).one()
    assert entry.id == "hf-co-zzauto-model-gguf-q4km"
    assert (entry.source_type, entry.source_ref, entry.source_revision) == (
        "huggingface", "zzauto/Model-GGUF", "0a1b2c3d")
    files = db.query(CatalogArtifactFile).filter_by(catalog_id=entry.id).all()
    assert [(f.file, f.role, f.sha256, f.size_bytes) for f in files] == [
        ("Model-IQ3_M.gguf", "weights", "a7" * 32, SIZE)]
