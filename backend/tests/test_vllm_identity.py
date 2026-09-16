"""vLLM identity (#116 step 4): shard lists on the wire and in the DB, the
resolver confirming an HF repo from daemon hints at a pinned revision with
every shard present, and auto-adopt building a vLLM entry from them."""
import json

import httpx
import pytest
from sqlalchemy.orm import sessionmaker

from catalog_service import auto_adopt_pass
from identity_resolver import Confirmed, IdentityResolver, Unconfirmed
from models import CatalogArtifactFile, ModelCatalog, RuntimeModel, WorkerRuntime
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
