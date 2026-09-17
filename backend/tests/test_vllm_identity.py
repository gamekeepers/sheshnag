"""vLLM identity (#116 step 4): shard lists on the wire and in the DB, the
resolver confirming an HF repo from daemon hints at a pinned revision with
every shard present, and auto-adopt building a vLLM entry from them."""
import json

import httpx
import pytest
from sqlalchemy.orm import sessionmaker

from catalog_service import auto_adopt_pass
from identity_resolver import Confirmed, IdentityResolver, Unconfirmed
from models import (CatalogArtifactFile, ModelCatalog, RuntimeModel,
                    ServingProfile, WorkerRuntime)
from provider_picker import can_serve, resolve_runtime_model_id
from reconciliation import AVAILABLE, UNREGISTERED

SHARD1 = "1" * 64
SHARD2 = "2" * 64
REV = "0a1b2c3d4e5f60718293a4b5c6d7e8f9a0b1c2d3"
FILES = [{"file": "model-00001-of-00002.safetensors", "sha256": SHARD1, "size_bytes": 1000},
         {"file": "model-00002-of-00002.safetensors", "sha256": SHARD2, "size_bytes": 500}]
DETAILS = {"quantization": "bf16", "parameter_size": "1.2B", "family": "llama",
           "context_length": 8192, "source_ref": "zzauto/Model", "source_revision": REV}


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
    return auth_client.post(f"/v1/orgs/{org['id']}/api-keys",
                            json={"name": "k", "key_type": "worker"}).json()["api_key"]


def _register_vllm(auth_client, key, hostname, inventory):
    resp = auth_client.post(
        "/workers/register",
        json={"hostname": hostname,
              "runtimes": [{"type": "vllm", "endpoint": "localhost:8000", "models": [],
                            "inventory": inventory}]},
        headers={"Authorization": f"Bearer {key}"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["worker_id"]


def _rows(db, worker_id):
    db.expire_all()
    return {m.name: m for rt in db.query(WorkerRuntime).filter_by(worker_id=worker_id)
            for m in db.query(RuntimeModel).filter_by(runtime_id=rt.id)}


def _item(name):
    return {"local_name": name, "sha256": SHARD1, "size_bytes": 1000, "loaded": True,
            "runtime": "vllm", "details": DETAILS, "files": FILES}


# ─── wire + DB ───────────────────────────────────────────────

def test_shard_list_is_stored_per_row(auth_client, db):
    key = _worker_key(auth_client, "vLLM Org 1")
    wid = _register_vllm(auth_client, key, "zzvllm-1", [_item("served-alias"), _item("zzauto/Model")])
    rows = _rows(db, wid)
    for name in ("served-alias", "zzauto/Model"):
        assert rows[name].digest == SHARD1
        assert rows[name].files == FILES
        assert rows[name].status == UNREGISTERED          # hash known, catalogue doesn't know it yet


# ─── resolver with hints ─────────────────────────────────────

def _hf_client(tree, status=200):
    def handler(request):
        assert request.url.host == "huggingface.co"
        if "/tree/" in request.url.path:
            assert request.url.path.endswith(f"/tree/{REV}")   # pinned revision, no /api/models info call
            return httpx.Response(status, json=tree)
        raise AssertionError(f"unexpected {request.url}")
    return httpx.Client(transport=httpx.MockTransport(handler))


def _tree(*oids):
    return [{"type": "file", "path": f"model-0000{i + 1}-of-00002.safetensors",
             "lfs": {"oid": oid, "size": 1}} for i, oid in enumerate(oids)] + [
            {"type": "file", "path": "config.json", "oid": "abc"}]


def test_resolver_confirms_all_shards_at_hinted_revision():
    r = IdentityResolver(client=_hf_client(_tree(SHARD1, SHARD2)))
    res = r.resolve("served-alias", SHARD1, source_ref="zzauto/Model", source_revision=REV,
                    files=[SHARD1, SHARD2])
    assert res == Confirmed(source_type="huggingface", source_ref="zzauto/Model",
                            source_revision=REV, digest=SHARD1,
                            homepage_url="https://huggingface.co/zzauto/Model",
                            source_file="model-00001-of-00002.safetensors")


def test_resolver_requires_every_shard():
    """One shard missing upstream (partial/mixed download) -> not the public artifact."""
    r = IdentityResolver(client=_hf_client(_tree(SHARD1)))
    res = r.resolve("served-alias", SHARD1, source_ref="zzauto/Model", source_revision=REV,
                    files=[SHARD1, SHARD2])
    assert res == Unconfirmed("digest-mismatch")


def test_resolver_hint_beats_name_parsing():
    """`zzauto/Model` alone parses as an Ollama community name; the hint says HF."""
    r = IdentityResolver(client=_hf_client(_tree(SHARD1)))
    assert isinstance(r.resolve("zzauto/Model", SHARD1, source_ref="zzauto/Model",
                                source_revision=REV), Confirmed)


# ─── auto-adopt for vLLM ─────────────────────────────────────

class StubResolver:
    def __init__(self, confirmed):
        self.confirmed = confirmed
        self.hints = []

    def resolve(self, name, sha256, **hints):
        # Other test modules leave quarantined rows behind (shared DB):
        # confirm only our artifact, or the pass would adopt theirs too.
        if sha256 != SHARD1:
            return Unconfirmed("digest-mismatch")
        self.hints.append(hints)
        return self.confirmed


def test_auto_adopt_builds_vllm_entry_from_hints(auth_client, db):
    key = _worker_key(auth_client, "vLLM Org 2")
    wid = _register_vllm(auth_client, key, "zzvllm-2", [_item("served-alias"), _item("zzauto/Model")])
    resolver = StubResolver(Confirmed(
        source_type="huggingface", source_ref="zzauto/Model", source_revision=REV,
        digest=SHARD1, homepage_url="https://huggingface.co/zzauto/Model",
        source_file="model-00001-of-00002.safetensors",
    ))
    assert auto_adopt_pass(db, resolver, enabled=True) == 1
    # Hints reached the resolver: repo, revision, every shard.
    assert resolver.hints[0] == {"source_ref": "zzauto/Model", "source_revision": REV,
                                 "files": [SHARD1, SHARD2]}

    entry = db.query(ModelCatalog).filter_by(digest=SHARD1).one()
    assert entry.id == "zzauto-model-bf16"                 # slug from the public repo id
    assert entry.runtime == "vllm"
    # The profile pins the served alias — what dispatch sends — not the repo path.
    assert [(p.runtime, p.runtime_model_id) for p in entry.profiles] == [("vllm", "served-alias")]
    assert (entry.source_type, entry.source_ref, entry.source_revision) == (
        "huggingface", "zzauto/Model", REV)
    assert entry.parameter_size == "1.2B" and entry.context_length == 8192
    files = db.query(CatalogArtifactFile).filter_by(catalog_id=entry.id).order_by(
        CatalogArtifactFile.file).all()
    assert [(f.file, f.role, f.sha256) for f in files] == [
        ("model-00001-of-00002.safetensors", "weights", SHARD1),
        ("model-00002-of-00002.safetensors", "shard", SHARD2),
    ]
    rows = _rows(db, wid)
    assert rows["served-alias"].status == AVAILABLE and rows["served-alias"].catalog_id == entry.id
    assert rows["zzauto/Model"].status == AVAILABLE


# ─── Multi-name serving (#124 review) ───────────────────────
# One artifact, several boxes, several --served-model-name aliases: one entry
# whose profile answers to every reported name, and a picker that follows the
# digest, not just the profiled ids.

MSHARD = "9f" * 32
MREV = "0f1e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c"
MFILES = [{"file": "model-00001-of-00001.safetensors", "sha256": MSHARD, "size_bytes": 1000}]
MDETAILS = {"quantization": "bf16", "parameter_size": "1.2B", "family": "llama",
            "context_length": 8192, "source_ref": "zzauto/Multi", "source_revision": MREV}


def _mitem(name):
    return {"local_name": name, "sha256": MSHARD, "size_bytes": 1000, "loaded": False,
            "runtime": "vllm", "details": MDETAILS, "files": MFILES}


def _heartbeat(auth_client, key, worker_id, inventory):
    resp = auth_client.post(
        f"/workers/{worker_id}/heartbeat",
        json={"worker_id": worker_id, "activity": "idle",
              "loaded_models": [], "inventory": inventory},
        headers={"Authorization": f"Bearer {key}"},
    )
    assert resp.status_code == 200, resp.text


class MultiResolver:
    def resolve(self, name, sha256, **hints):
        if sha256 != MSHARD:
            return Unconfirmed("digest-mismatch")
        return Confirmed(source_type="huggingface", source_ref="zzauto/Multi",
                         source_revision=MREV, digest=MSHARD,
                         homepage_url="https://huggingface.co/zzauto/Multi",
                         source_file="model-00001-of-00001.safetensors")


def test_auto_adopt_pins_every_reported_alias(auth_client, db):
    """Two boxes, two aliases, one artifact: the entry pins the shortest
    alias as primary and every other reported name as an extra — all of them
    stay dispatch targets on the single entry."""
    key1 = _worker_key(auth_client, "vLLM Org 3")
    _register_vllm(auth_client, key1, "zzvllm-m1", [_mitem("zeta-alias"), _mitem("zzauto/Multi")])
    key2 = _worker_key(auth_client, "vLLM Org 4")
    wid2 = _register_vllm(auth_client, key2, "zzvllm-m2", [_mitem("ab"), _mitem("zzauto/Multi")])
    assert auto_adopt_pass(db, MultiResolver(), enabled=True) == 1

    entry = db.query(ModelCatalog).filter_by(digest=MSHARD).one()
    assert len(entry.profiles) == 1
    profile = entry.profiles[0]
    assert profile.runtime == "vllm"
    assert profile.runtime_model_id == "ab"      # shortest alias wins, deterministically
    assert profile.runtime_model_ids == ["zeta-alias", "zzauto/Multi"]
    assert {rmid for _rt, rmid in entry.serving_targets()} >= {"ab", "zeta-alias", "zzauto/Multi"}
    rows = _rows(db, wid2)
    assert rows["ab"].status == AVAILABLE and rows["ab"].catalog_id == entry.id


def test_picker_digest_join_for_unprofiled_alias(auth_client, db):
    """A box hosting the byte-identical artifact under a name the entry does
    not profile is still a host (digest IS the identity), and dispatch sends
    the name that box actually answers to — never a profiled id it doesn't
    have. The same-tag-different-digest guard stays intact."""
    e = ModelCatalog(id="zzauto-pick-bf16", display_name="Pick", runtime="vllm",
                     runtime_model_id="alpha", digest=MSHARD, vram_gb=1.0,
                     enabled=True, status="active")
    db.add(e)
    db.flush()
    db.add(ServingProfile(catalog_id=e.id, runtime="vllm", runtime_model_id="alpha"))
    db.commit()
    other = "ee" * 32
    assert can_serve(e, [("beta", MSHARD)], 16)
    assert resolve_runtime_model_id(e, [("beta", MSHARD)]) == "beta"
    assert not can_serve(e, [("beta", other)], 16)
    assert resolve_runtime_model_id(e, [("beta", other), ("alpha", MSHARD)]) == "alpha"
    assert not can_serve(e, [("alpha", other)], 16)     # same tag, different digest
    assert can_serve(e, [("alpha", None)], 16)          # old daemon: name fallback


def test_heartbeat_extends_profile_with_new_alias(auth_client, db):
    """A box that reports the pinned hash under an unprofiled alias extends
    the entry's profile for its own runtime — and only for that runtime: a
    first profile for a new runtime is a curation decision, not a heartbeat's
    call."""
    e = ModelCatalog(id="zzauto-hb-bf16", display_name="HB", runtime="vllm",
                     runtime_model_id="alpha", digest=MSHARD, vram_gb=1.0,
                     enabled=True, status="active")
    db.add(e)
    db.flush()
    db.add(ServingProfile(catalog_id=e.id, runtime="vllm", runtime_model_id="alpha"))
    db.commit()
    key = _worker_key(auth_client, "vLLM Org 5")
    wid = _register_vllm(auth_client, key, "zzvllm-hb", [_mitem("beta")])
    rows = _rows(db, wid)
    assert rows["beta"].status == AVAILABLE and rows["beta"].catalog_id == e.id
    # The reconciliation loop runs on the heartbeat, not registration.
    _heartbeat(auth_client, key, wid, [_mitem("beta")])
    profile = db.query(ServingProfile).filter_by(catalog_id=e.id, runtime="vllm").one()
    assert profile.runtime_model_ids == ["beta"]

    org = auth_client.post("/v1/me/organizations", json={"name": "vLLM Org 6"}).json()
    key_o = auth_client.post(f"/v1/orgs/{org['id']}/api-keys",
                             json={"name": "k", "key_type": "worker"}).json()["api_key"]
    wid_o = auth_client.post(
        "/workers/register",
        json={"hostname": "zzollama-hb",
              "runtimes": [{"type": "ollama", "endpoint": "localhost", "models": [],
                            "inventory": [{"local_name": "gamma", "sha256": MSHARD,
                                           "size_bytes": 1, "loaded": False,
                                           "runtime": "ollama", "details": None}]}]},
        headers={"Authorization": f"Bearer {key_o}"}).json()["worker_id"]
    rows_o = _rows(db, wid_o)
    assert rows_o["gamma"].status == AVAILABLE and rows_o["gamma"].catalog_id == e.id
    r_o = auth_client.post(
        f"/workers/{wid_o}/heartbeat",
        json={"worker_id": wid_o, "activity": "idle", "loaded_models": [],
              "inventory": [{"local_name": "gamma", "sha256": MSHARD, "size_bytes": 1,
                             "loaded": False, "runtime": "ollama", "details": None}]},
        headers={"Authorization": f"Bearer {key_o}"})
    assert r_o.status_code == 200, r_o.text
    assert db.query(ServingProfile).filter_by(catalog_id=e.id, runtime="ollama").first() is None
