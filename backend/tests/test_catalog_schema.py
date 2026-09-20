"""Registry schema (#116): naming validation, serving profiles, artifact
files, and the one-shot slug rename in the catalogue seed.

All rows created here use the `zztest-` prefix and are deleted afterwards:
the test database is session-scoped with no per-test truncation, and
test_models.py's seed fixture skips seeding when the catalogue is non-empty.
"""
import os
from types import SimpleNamespace

import pytest
import yaml
from sqlalchemy.orm import sessionmaker

import catalog_seed
from catalog_seed import seed_model_catalog, validate_entry_id
from models import CatalogArtifactFile, ModelCatalog, ServingProfile


@pytest.fixture
def db(_engine):
    SM = sessionmaker(bind=_engine)
    session = SM()
    yield session
    session.rollback()
    test_ids = [
        row.id for row in
        session.query(ModelCatalog).filter(ModelCatalog.id.like("zztest%"))
    ]
    if test_ids:
        # FK ondelete=CASCADE clears profile/file children.
        session.query(ModelCatalog).filter(ModelCatalog.id.in_(test_ids)).delete(
            synchronize_session=False
        )
        session.commit()
    session.close()


def _seed(monkeypatch, tmp_path, entries):
    import yaml
    manifest = tmp_path / "models.yaml"
    manifest.write_text(yaml.safe_dump(entries, sort_keys=False))
    monkeypatch.setattr(catalog_seed, "_MANIFEST", str(manifest))
    seed_model_catalog()


# ─── Naming rules ────────────────────────────────────────────

def test_validate_entry_id_rules():
    ok = {"quantization": "Q4_K_M"}
    assert validate_entry_id("qwen3-4b-q4km", ok) == []
    # runtime name in a segment
    assert any("runtime" in e for e in validate_entry_id("qwen3-4b-ollama", {}))
    # uppercase / illegal chars
    assert validate_entry_id("Qwen3-4B", {}) != []
    assert validate_entry_id("qwen3_4b", {}) != []
    # quant suffix required when quantization declared
    assert any("quant slug" in e for e in validate_entry_id("qwen3-4b", ok))
    # no quantization declared -> no suffix requirement
    assert validate_entry_id("nomic-embed-text", {}) == []


def test_seed_skips_entry_violating_naming(db, monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, [{
        "id": "zztest-bad-ollama",
        "display_name": "bad",
        "runtime": "ollama",
        "runtime_model_id": "bad:1b",
    }])
    assert db.query(ModelCatalog).filter(
        ModelCatalog.id == "zztest-bad-ollama"
    ).first() is None


# ─── Serving profiles ────────────────────────────────────────

def test_legacy_runtime_pair_becomes_profile(db, monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, [{
        "id": "zztest-chat-q4km",
        "display_name": "chat",
        "runtime": "ollama",
        "runtime_model_id": "chat:4b",
        "quantization": "Q4_K_M",
        "capabilities": {"json_mode": True},
        "lineage": "example/chat-4b",
    }])
    entry = db.query(ModelCatalog).filter(ModelCatalog.id == "zztest-chat-q4km").one()
    assert entry.capabilities == {"json_mode": True}
    assert entry.lineage == "example/chat-4b"
    assert [(p.runtime, p.runtime_model_id) for p in entry.profiles] == [
        ("ollama", "chat:4b")
    ]
    assert entry.serving_targets() == [("ollama", "chat:4b")]


def test_explicit_profiles_sync_and_prune(db, monkeypatch, tmp_path):
    base = {
        "id": "zztest-multi-q4km",
        "display_name": "multi",
        "runtime": "ollama",
        "runtime_model_id": "multi:4b",
        "quantization": "Q4_K_M",
    }
    two = dict(base, profiles=[
        {"runtime": "ollama", "runtime_model_id": "multi:4b"},
        {"runtime": "llamacpp", "runtime_model_id": "repo/multi-4b-Q4_K_M.gguf",
         "params": {"n_ctx": 8192, "parallel": 8}},
    ])
    _seed(monkeypatch, tmp_path, [two])
    profiles = db.query(ServingProfile).filter(
        ServingProfile.catalog_id == "zztest-multi-q4km"
    ).all()
    assert {p.runtime for p in profiles} == {"ollama", "llamacpp"}
    llamacpp = next(p for p in profiles if p.runtime == "llamacpp")
    assert llamacpp.params == {"n_ctx": 8192, "parallel": 8}

    # Manifest owns profiles: dropping one removes its row.
    one = dict(base, profiles=[
        {"runtime": "llamacpp", "runtime_model_id": "repo/multi-4b-Q4_K_M.gguf"},
    ])
    _seed(monkeypatch, tmp_path, [one])
    db.expire_all()
    profiles = db.query(ServingProfile).filter(
        ServingProfile.catalog_id == "zztest-multi-q4km"
    ).all()
    assert [(p.runtime, p.params) for p in profiles] == [("llamacpp", None)]


def test_malformed_profiles_are_skipped_not_fatal(db, monkeypatch, tmp_path):
    """A hand-edited manifest with a junk profile entry must degrade to a
    logged skip — not an AttributeError, not an IntegrityError at commit."""
    _seed(monkeypatch, tmp_path, [{
        "id": "zztest-junk-q4km",
        "display_name": "junk",
        "runtime": "ollama",
        "runtime_model_id": "junk:1b",
        "quantization": "Q4_K_M",
        "profiles": [
            "not-a-mapping",                                        # non-dict
            {"runtime": "ollama", "runtime_model_id": "junk:1b"},   # valid
            {"runtime": "ollama", "runtime_model_id": "junk:2b"},   # dup runtime
            {"runtime": "vllm"},                                    # missing rmid
        ],
    }])
    entry = db.query(ModelCatalog).filter(ModelCatalog.id == "zztest-junk-q4km").one()
    # Only the first valid profile per runtime survives.
    assert [(p.runtime, p.runtime_model_id) for p in entry.profiles] == [
        ("ollama", "junk:1b")
    ]


# ─── Artifact files ──────────────────────────────────────────

def test_multi_file_artifact_sync(db, monkeypatch, tmp_path):
    entry = {
        "id": "zztest-vision-q4km",
        "display_name": "vision",
        "runtime": "ollama",
        "runtime_model_id": "vision:4b",
        "quantization": "Q4_K_M",
        "files": [
            {"file": "vision-Q4_K_M.gguf", "role": "weights",
             "sha256": "a" * 64, "size_bytes": 1000},
            {"file": "mmproj-f16.gguf", "role": "mmproj",
             "sha256": "b" * 64, "size_bytes": 200},
        ],
    }
    _seed(monkeypatch, tmp_path, [entry])
    rows = db.query(CatalogArtifactFile).filter(
        CatalogArtifactFile.catalog_id == "zztest-vision-q4km"
    ).all()
    assert {(r.file, r.role, r.sha256) for r in rows} == {
        ("vision-Q4_K_M.gguf", "weights", "a" * 64),
        ("mmproj-f16.gguf", "mmproj", "b" * 64),
    }

    # Re-seed with a changed hash and one file removed: row updated, row pruned.
    entry["files"] = [
        {"file": "vision-Q4_K_M.gguf", "role": "weights",
         "sha256": "c" * 64, "size_bytes": 1000},
    ]
    _seed(monkeypatch, tmp_path, [entry])
    db.expire_all()
    rows = db.query(CatalogArtifactFile).filter(
        CatalogArtifactFile.catalog_id == "zztest-vision-q4km"
    ).all()
    assert [(r.file, r.sha256) for r in rows] == [("vision-Q4_K_M.gguf", "c" * 64)]


# ─── Rename (one-shot slug migration) ────────────────────────

def test_renamed_from_updates_row_and_children(db, monkeypatch, tmp_path):
    db.add(ModelCatalog(
        id="zztest-old-name", display_name="old",
        runtime="ollama", runtime_model_id="old:1b",
    ))
    db.flush()
    db.add(ServingProfile(
        catalog_id="zztest-old-name", runtime="ollama", runtime_model_id="old:1b",
    ))
    db.commit()

    _seed(monkeypatch, tmp_path, [{
        "id": "zztest-new-q4km",
        "renamed_from": "zztest-old-name",
        "display_name": "new",
        "runtime": "ollama",
        "runtime_model_id": "old:1b",
        "quantization": "Q4_K_M",
    }])
    db.expire_all()
    assert db.query(ModelCatalog).filter(
        ModelCatalog.id == "zztest-old-name"
    ).first() is None
    renamed = db.query(ModelCatalog).filter(
        ModelCatalog.id == "zztest-new-q4km"
    ).one()
    assert renamed.display_name == "new"
    # Child rows follow the rename instead of being orphaned/duplicated.
    profiles = db.query(ServingProfile).filter(
        ServingProfile.catalog_id == "zztest-new-q4km"
    ).all()
    assert [(p.runtime, p.runtime_model_id) for p in profiles] == [("ollama", "old:1b")]
    assert db.query(ServingProfile).filter(
        ServingProfile.catalog_id == "zztest-old-name"
    ).count() == 0


def test_renamed_from_is_noop_without_old_row(db, monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, [{
        "id": "zztest-fresh-q4km",
        "renamed_from": "zztest-never-existed",
        "display_name": "fresh",
        "runtime": "ollama",
        "runtime_model_id": "fresh:1b",
        "quantization": "Q4_K_M",
    }])
    assert db.query(ModelCatalog).filter(
        ModelCatalog.id == "zztest-fresh-q4km"
    ).one().display_name == "fresh"


# ─── Scheduler integration ───────────────────────────────────

def test_hosts_matches_any_serving_target(db, monkeypatch, tmp_path):
    from scheduler import can_serve

    _seed(monkeypatch, tmp_path, [{
        "id": "zztest-dual-q4km",
        "display_name": "dual",
        "runtime": "ollama",
        "runtime_model_id": "dual:4b",
        "quantization": "Q4_K_M",
        "vram_gb": 4.0,
        "profiles": [
            {"runtime": "ollama", "runtime_model_id": "dual:4b"},
            {"runtime": "llamacpp", "runtime_model_id": "repo/dual-Q4_K_M.gguf"},
        ],
    }])
    entry = db.query(ModelCatalog).filter(ModelCatalog.id == "zztest-dual-q4km").one()

    # A worker advertising EITHER runtime's id can serve the entry.
    assert can_serve(entry, _worker_hosting([("dual:4b", None)], 8.0))
    assert can_serve(entry, _worker_hosting([("repo/dual-Q4_K_M.gguf", None)], 8.0))
    assert not can_serve(entry, _worker_hosting([("other:7b", None)], 8.0))
    # VRAM gate still applies.
    assert not can_serve(entry, _worker_hosting([("dual:4b", None)], 2.0))

    # Dispatch hands each worker the id IT hosts — not the legacy column.
    from scheduler import resolve_runtime_model_id
    assert resolve_runtime_model_id(entry, [("dual:4b", None)]) == "dual:4b"
    assert resolve_runtime_model_id(
        entry, [("repo/dual-Q4_K_M.gguf", None)]
    ) == "repo/dual-Q4_K_M.gguf"
    # No match (pre-heartbeat worker, empty model list) -> legacy fallback.
    assert resolve_runtime_model_id(entry, []) == "dual:4b"


def _worker_hosting(models, vram_gb, engine="ollama"):
    """A worker hosting `(name, digest)` pairs on one runtime with one card.

    `can_serve` takes the worker now, not a flattened model list, because the
    fit rule depends on which runtime hosts the artifact. These cases are about
    name/digest matching, so one ready runtime and one card is enough.
    """
    return SimpleNamespace(
        gpus=[SimpleNamespace(vram_gb=vram_gb)],
        runtimes=[SimpleNamespace(
            engine=engine, schedulable=True,
            models=[SimpleNamespace(name=n, digest=d, schedulable=True)
                    for n, d in models],
        )],
        vram_total_gb=vram_gb, ram_total_gb=None, ram_available_gb=None,
    )


# ── The shipped manifest ─────────────────────────────────────────
#
# These read `catalog/models.yaml` itself and never seed it: the test
# database is session-scoped, so inserting the real catalogue would leave
# rows behind and suppress test_models.py's seed fixture.

def _manifest():
    path = os.path.join(os.path.dirname(catalog_seed.__file__), "catalog", "models.yaml")
    with open(path) as fh:
        return yaml.safe_load(fh)


def test_shipped_manifest_ids_all_validate():
    """A hand-edit that breaks a naming rule is a silently skipped entry —
    the model disappears from the catalogue without the boot failing."""
    offenders = {
        entry["id"]: validate_entry_id(entry["id"], entry)
        for entry in _manifest()
        if validate_entry_id(entry["id"], entry)
    }
    assert offenders == {}


def test_shipped_manifest_profiles_all_normalise():
    """Every entry yields at least one profile, whether it declares
    `profiles:` or relies on the legacy runtime pair."""
    for entry in _manifest():
        assert catalog_seed._entry_profiles(entry), entry["id"]


def test_llamacpp_entry_is_servable_as_written():
    entry = next(e for e in _manifest() if e["id"] == "qwen36-27b-q4kxl")

    # NOT NULL on both columns: an entry with only `profiles:` fails to insert.
    assert entry["runtime"] and entry["runtime_model_id"]

    profiles = catalog_seed._entry_profiles(entry)
    assert [(p["runtime"], p["runtime_model_id"]) for p in profiles] == [
        ("llamacpp", "qwen36-27b-q4kxl")
    ]
    # The served name is a contract on the provider's --alias flag.
    assert entry["runtime_model_id"] == profiles[0]["runtime_model_id"]
    # Weighed against VRAM + usable RAM, so it is the whole footprint and
    # must exceed the weights on disk.
    assert entry["vram_gb"] > entry["size_gb"]
