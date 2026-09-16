"""
Seed / sync the model catalogue from the manifest at startup.

Source of truth is `catalog/models.yaml` (version-controlled). On startup:
  - new `id`s are inserted,
  - existing `id`s have their manifest-managed fields updated (edit the
    YAML, restart to apply),
  - an entry carrying `renamed_from: <old-id>` renames the existing row in
    place (one-shot slug migration — no aliases table, by decision on
    #116; `batches.model` is a plain string, so history keeps old slugs),
  - entries NOT in the manifest are left untouched — so admin-added or
    org-private entries are never clobbered.

Each entry also owns child rows, synced on every seed:
  - `profiles:` → `serving_profiles` (runtime binding + launch knobs).
    Entries without an explicit `profiles:` list get one profile derived
    from the legacy top-level `runtime` / `runtime_model_id` pair.
  - `files:` → `catalog_artifact_files` (multi-file artifacts: a vision
    GGUF is weights + mmproj; safetensors models are many shards).

Naming rules (#116) are enforced on every manifest id: lowercase
`[a-z0-9-]`, no runtime name as a segment, and — when the entry declares a
`quantization` — the id must end in that quant's slug (`Q4_K_M` → `-q4km`).
A violating entry is skipped with an error rather than failing the boot.

`digest` is the artifact FILE's sha256 (what daemons report in their
inventory) — fill it via `python -m scripts.capture_catalog`, which reads
it from Ollama manifests (local tree or registry), never from /api/tags.
A null digest leaves the entry on name matching.
"""
import logging
import os
import re

from database import SessionLocal
from models import CatalogArtifactFile, ModelCatalog, ServingProfile

logger = logging.getLogger(__name__)

_MANIFEST = os.path.join(os.path.dirname(__file__), "catalog", "models.yaml")

# Fields the manifest owns — updated on re-seed. `id` is the key (not
# updated; `renamed_from` handles renames); `created_at`/`status`/`org_id`
# are managed elsewhere. `enabled` is handled separately (default True
# unless the entry explicitly sets it false — how discover-staged stubs
# stay inactive until promoted).
_MANAGED_FIELDS = (
    "display_name", "runtime", "runtime_model_id", "digest", "quantization",
    "parameter_size", "context_length", "vram_gb", "size_gb", "task_type",
    "capabilities", "lineage",
    "source_type", "source_ref", "source_revision", "homepage_url",
)

_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

# Runtime names may never appear in a platform slug: the id must survive an
# Ollama → llama.cpp swap unchanged. (Ids are compared segment-wise, so only
# hyphen-free names can ever match.)
_RUNTIME_SEGMENTS = frozenset({"ollama", "vllm", "llamacpp", "tgi"})

_ARTIFACT_ROLES = frozenset({"weights", "mmproj", "shard"})


def _quant_slug(quantization: str) -> str:
    """`Q4_K_M` → `q4km`: lowercase, strip separators."""
    return re.sub(r"[^a-z0-9]", "", str(quantization).lower())


def validate_entry_id(mid: str, entry: dict) -> list:
    """Naming-rule violations for a manifest id ([] = valid)."""
    errors = []
    if not _ID_RE.match(mid):
        errors.append("id must be lowercase [a-z0-9] segments joined by '-'")
    segments = set(mid.split("-"))
    hit = segments & _RUNTIME_SEGMENTS
    if hit:
        errors.append(f"id must not contain a runtime name ({', '.join(sorted(hit))})")
    quant = entry.get("quantization")
    if quant and not mid.endswith("-" + _quant_slug(quant)):
        errors.append(
            f"id must end with the quant slug '-{_quant_slug(quant)}' "
            f"(quantization: {quant})"
        )
    return errors


def _load_manifest() -> list:
    try:
        import yaml
    except ImportError:
        logger.warning("PyYAML not installed — skipping catalogue seed")
        return []
    if not os.path.exists(_MANIFEST):
        logger.warning("Catalogue manifest not found at %s — skipping seed", _MANIFEST)
        return []
    with open(_MANIFEST) as f:
        data = yaml.safe_load(f) or []
    if not isinstance(data, list):
        logger.error("Catalogue manifest must be a list of entries — skipping seed")
        return []
    return data


def _entry_profiles(entry: dict) -> list:
    """Normalised `profiles:` for an entry; legacy pair becomes one profile."""
    profiles = entry.get("profiles")
    if not profiles:
        if entry.get("runtime") and entry.get("runtime_model_id"):
            profiles = [{
                "runtime": entry["runtime"],
                "runtime_model_id": entry["runtime_model_id"],
            }]
        else:
            return []
    out = []
    seen_runtimes = set()
    for p in profiles:
        # Manifest is hand-edited YAML: a malformed profile must degrade to
        # a logged skip, never an exception or an IntegrityError at commit
        # (the seed's "boot never fails" guarantee).
        if not isinstance(p, dict):
            logger.warning(
                "Catalogue entry %r: profile is not a mapping — skipped: %r",
                entry.get("id"), p,
            )
            continue
        if not p.get("runtime") or not p.get("runtime_model_id"):
            logger.warning(
                "Catalogue entry %r: profile missing runtime/runtime_model_id "
                "— skipped: %r", entry.get("id"), p,
            )
            continue
        if p["runtime"] in seen_runtimes:
            logger.warning(
                "Catalogue entry %r: duplicate profile for runtime %r — "
                "first wins, duplicate skipped", entry.get("id"), p["runtime"],
            )
            continue
        seen_runtimes.add(p["runtime"])
        out.append({
            "runtime": p["runtime"],
            "runtime_model_id": p["runtime_model_id"],
            "params": p.get("params"),
        })
    return out


def _sync_profiles(db, mid: str, wanted: list) -> bool:
    """Make `serving_profiles` for `mid` match the manifest. True if changed."""
    existing = {
        p.runtime: p
        for p in db.query(ServingProfile).filter(ServingProfile.catalog_id == mid)
    }
    changed = False
    seen = set()
    for w in wanted:
        seen.add(w["runtime"])
        row = existing.get(w["runtime"])
        if row is None:
            db.add(ServingProfile(catalog_id=mid, **w))
            changed = True
        elif (row.runtime_model_id, row.params) != (w["runtime_model_id"], w["params"]):
            row.runtime_model_id = w["runtime_model_id"]
            row.params = w["params"]
            changed = True
    for runtime, row in existing.items():
        if runtime not in seen:
            db.delete(row)  # manifest owns profiles — removed there = removed here
            changed = True
    return changed


def _sync_files(db, mid: str, entry: dict) -> bool:
    """Make `catalog_artifact_files` for `mid` match the manifest. True if changed."""
    wanted = {}
    for f in entry.get("files") or []:
        name = f.get("file")
        if not name:
            logger.warning("Catalogue entry %r: files item missing 'file' — skipped", mid)
            continue
        role = f.get("role", "weights")
        if role not in _ARTIFACT_ROLES:
            logger.warning(
                "Catalogue entry %r: unknown artifact role %r for %r — skipped",
                mid, role, name,
            )
            continue
        wanted[name] = {
            "role": role,
            "sha256": f.get("sha256"),
            "size_bytes": f.get("size_bytes"),
        }

    existing = {
        r.file: r
        for r in db.query(CatalogArtifactFile).filter(CatalogArtifactFile.catalog_id == mid)
    }
    changed = False
    for name, w in wanted.items():
        row = existing.get(name)
        if row is None:
            db.add(CatalogArtifactFile(catalog_id=mid, file=name, **w))
            changed = True
        elif (row.role, row.sha256, row.size_bytes) != (w["role"], w["sha256"], w["size_bytes"]):
            row.role, row.sha256, row.size_bytes = w["role"], w["sha256"], w["size_bytes"]
            changed = True
    for name, row in existing.items():
        if name not in wanted:
            db.delete(row)
            changed = True
    return changed


def _apply_rename(db, mid: str, entry: dict) -> None:
    """Rename an existing row to `mid` when the entry declares `renamed_from`.

    Implemented as insert-under-new-id, repoint children, delete old row
    (the child FKs have no ON UPDATE CASCADE, so the PK cannot change in
    place). `batches.model` is a plain string — historical rows keep the
    retired slug, which dashboards may display as-is.
    """
    old_id = entry.get("renamed_from")
    if not old_id:
        return
    if db.query(ModelCatalog).filter(ModelCatalog.id == mid).first() is not None:
        # Normally "already renamed" — but if the OLD id also still exists
        # (e.g. capture_catalog --discover re-staged it), a duplicate active
        # entry is stranded; that needs an operator, so say so.
        if db.query(ModelCatalog).filter(ModelCatalog.id == old_id).first() is not None:
            logger.warning(
                "Catalogue rename %s -> %s skipped: BOTH ids exist — "
                "duplicate entry stranded under the old id; delete or "
                "disable %r manually", old_id, mid, old_id,
            )
        return
    old = db.query(ModelCatalog).filter(ModelCatalog.id == old_id).first()
    if old is None:
        return  # nothing to rename — new install, plain insert follows

    # The FK from child tables has no ON UPDATE CASCADE, so the PK cannot be
    # updated in place while children reference it: insert the row under the
    # new id, repoint the children, then drop the old row.
    values = {
        c.name: getattr(old, c.name)
        for c in ModelCatalog.__table__.columns
        if c.name != "id"
    }
    db.add(ModelCatalog(id=mid, **values))
    db.flush()
    db.query(ServingProfile).filter(ServingProfile.catalog_id == old_id).update(
        {"catalog_id": mid}
    )
    db.query(CatalogArtifactFile).filter(CatalogArtifactFile.catalog_id == old_id).update(
        {"catalog_id": mid}
    )
    db.delete(old)
    db.flush()
    logger.info("Catalogue entry renamed: %s -> %s", old_id, mid)


def seed_model_catalog() -> None:
    """Upsert catalogue entries from the manifest. Safe to call every startup."""
    entries = _load_manifest()
    if not entries:
        return

    db = SessionLocal()
    try:
        inserted = updated = 0
        for entry in entries:
            mid = entry.get("id")
            if not mid:
                logger.warning("Catalogue entry missing 'id' — skipped: %r", entry)
                continue
            id_errors = validate_entry_id(mid, entry)
            if id_errors:
                logger.error(
                    "Catalogue entry %r violates naming rules — skipped: %s",
                    mid, "; ".join(id_errors),
                )
                continue

            _apply_rename(db, mid, entry)

            fields = {k: entry.get(k) for k in _MANAGED_FIELDS}
            # Default enabled=True unless the entry explicitly disables it
            # (discover-staged stubs set enabled:false to stay inactive).
            fields["enabled"] = bool(entry.get("enabled", True))

            existing = db.query(ModelCatalog).filter(ModelCatalog.id == mid).first()
            changed = False
            if existing is None:
                db.add(ModelCatalog(id=mid, **fields))
                inserted += 1
            else:
                for k, v in fields.items():
                    if getattr(existing, k) != v:
                        setattr(existing, k, v)
                        changed = True
            db.flush()  # entry row must exist before child FK rows

            changed |= _sync_profiles(db, mid, _entry_profiles(entry))
            changed |= _sync_files(db, mid, entry)
            if changed and existing is not None:
                updated += 1

        db.commit()
        if inserted or updated:
            logger.info("Model catalogue synced: %d inserted, %d updated", inserted, updated)
    finally:
        db.close()
