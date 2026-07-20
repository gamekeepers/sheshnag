"""
Seed / sync the model catalogue from the manifest at startup.

Source of truth is `catalog/models.yaml` (version-controlled). On startup:
  - new `id`s are inserted,
  - existing `id`s have their manifest-managed fields updated (edit the
    YAML, restart to apply),
  - entries NOT in the manifest are left untouched — so admin-added or
    org-private entries are never clobbered.

`digest` is intentionally often null in the manifest — fill it via
`python -m scripts.capture_catalog` (reads a live Ollama) to switch an
entry from name-matching to the strict digest reproducibility guard.
"""
import logging
import os

from database import SessionLocal
from models import ModelCatalog

logger = logging.getLogger(__name__)

_MANIFEST = os.path.join(os.path.dirname(__file__), "catalog", "models.yaml")

# Fields the manifest owns — updated on re-seed. `id` is the key (not
# updated); `created_at`/`status`/`org_id` are managed elsewhere. `enabled`
# is handled separately (default True unless the entry explicitly sets it
# false — how discover-staged stubs stay inactive until promoted).
_MANAGED_FIELDS = (
    "display_name", "runtime", "runtime_model_id", "digest", "quantization",
    "parameter_size", "context_length", "vram_gb", "size_gb", "task_type",
    "source_type", "source_ref", "source_revision", "homepage_url",
)


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
            fields = {k: entry.get(k) for k in _MANAGED_FIELDS}
            # Default enabled=True unless the entry explicitly disables it
            # (discover-staged stubs set enabled:false to stay inactive).
            fields["enabled"] = bool(entry.get("enabled", True))

            existing = db.query(ModelCatalog).filter(ModelCatalog.id == mid).first()
            if existing is None:
                db.add(ModelCatalog(id=mid, **fields))
                inserted += 1
            else:
                changed = False
                for k, v in fields.items():
                    if getattr(existing, k) != v:
                        setattr(existing, k, v)
                        changed = True
                if changed:
                    updated += 1

        if inserted or updated:
            db.commit()
            logger.info("Model catalogue synced: %d inserted, %d updated", inserted, updated)
    finally:
        db.close()
