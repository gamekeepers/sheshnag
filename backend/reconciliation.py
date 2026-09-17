"""
Reconcile what workers report they hold against what the registry pins (#116).

The catalogue is desired state ("this slug means exactly these bytes");
a worker's inventory is observed state ("this box holds these bytes").
Every reported artifact lands in one of four states on its `runtime_models`
row, and the picker routes only to the first:

  available     hash matches a catalogue pin (catalog_id set), or the row
                is unverifiable (no hash reported: old daemon, vLLM) and its
                name matches an entry — today's behaviour, unchanged
  unregistered  a reported HASH matches no entry (and the name matches no
                entry either): quarantined until an admin adopts it
                (POST /v1/models/adopt) or the blob is replaced. Rows with
                no hash at all are never quarantined — there is no identity
                to adopt, and the picker already requires a catalogue entry
                for the name, so nothing unknown can be scheduled anyway
  drift         name claims a pinned entry but the bytes differ from every
                pin under that name — never served; the picker also logs it
  missing       present in the DB but absent from a full inventory the
                worker just sent (e.g. `ollama rm`) — never served

Nothing here trusts a name as identity when a hash is available.
"""
import logging
from typing import Optional, Tuple

from identity_resolver import bare_digest
from models import CatalogArtifactFile, ModelCatalog, RuntimeModel, ServingProfile, unix_now

logger = logging.getLogger(__name__)

AVAILABLE = "available"
UNREGISTERED = "unregistered"
DRIFT = "drift"
MISSING = "missing"


# Digest normalisation lives in identity_resolver.bare_digest — one copy for
# the backend so worker rows, catalogue pins and registry answers all compare
# the same way.
_norm = bare_digest


def find_entry_by_hash(db, sha256) -> Optional[ModelCatalog]:
    """Catalogue entry whose weights digest or any artifact file matches.

    Deliberately ignores `enabled`/`status`: a disabled or deprecated entry
    still IDENTIFIES the bytes ("known, not offered"). Whether the entry may
    be scheduled is the picker's `get_catalog_entry` filter, not identity."""
    d = _norm(sha256)
    if not d:
        return None
    entry = db.query(ModelCatalog).filter(
        ModelCatalog.digest.in_([d, f"sha256:{d}"])
    ).first()
    if entry is not None:
        return entry
    f = db.query(CatalogArtifactFile).filter(
        CatalogArtifactFile.sha256.in_([d, f"sha256:{d}"])
    ).first()
    return f.entry if f is not None else None


def find_entries_by_name(db, runtime_model_id: str) -> list:
    """Catalogue entries that answer to this runtime id (profiles or legacy)."""
    ids = {
        p.catalog_id for p in
        db.query(ServingProfile.catalog_id).filter(
            ServingProfile.runtime_model_id == runtime_model_id
        )
    }
    ids |= {
        e.id for e in
        db.query(ModelCatalog.id).filter(ModelCatalog.runtime_model_id == runtime_model_id)
    }
    if not ids:
        return []
    return db.query(ModelCatalog).filter(ModelCatalog.id.in_(ids)).all()


def classify_entry(db, name: str, sha256) -> Tuple[str, Optional[ModelCatalog]]:
    """(status, matched entry or None) for one reported artifact.

    Hash first, always. With no hash the row is unverifiable: it stays
    `available` and name-matches exactly as before #116 (old daemons, vLLM,
    a worker registered before the catalogue entry landed), and is never
    quarantined — quarantine is for bytes we can identify but don't know.
    With a hash that matches nothing: an unknown name is unregistered; a
    name that claims only pinned entries is drift; a name that matches an
    unpinned entry still name-matches.
    """
    if not sha256:
        return AVAILABLE, None
    by_hash = find_entry_by_hash(db, sha256)
    if by_hash is not None:
        return AVAILABLE, by_hash
    by_name = find_entries_by_name(db, name)
    if not by_name:
        return UNREGISTERED, None
    if all(_norm(e.digest) for e in by_name):
        return DRIFT, None
    return AVAILABLE, None


def classify(db, name: str, sha256) -> Tuple[str, Optional[str]]:
    """(status, catalog_id) — see classify_entry."""
    status, entry = classify_entry(db, name, sha256)
    return status, (entry.id if entry is not None else None)


def local_names_for_hash(db, sha256) -> set:
    """Every local_name workers report for this hash (adopt validation)."""
    d = _norm(sha256)
    if not d:
        return set()
    return {
        r.name for r in
        db.query(RuntimeModel.name).filter(RuntimeModel.digest.in_([d, f"sha256:{d}"]))
    }


def _files_payload(item) -> Optional[list]:
    files = getattr(item, "files", None)
    if not files:
        return None
    return [f.model_dump() if hasattr(f, "model_dump") else dict(f) for f in files]


def _ensure_serving_name(entry, name: str, runtime_engine: str) -> bool:
    """Make `entry` answer to `name` on `runtime_engine`.

    The row's digest already matched this entry — proof the artifact is the
    pinned one — so `name` (another box's served alias) is a valid serving
    id for it. Appended to the entry's profile for that runtime so the box
    dispatches instead of sitting `available`-forever. Only extends a profile
    the entry already has for the runtime: a first profile for a new runtime
    is a curation decision (launch knobs and all), not something a heartbeat
    should invent. Returns True when the entry changed."""
    profile = next((p for p in entry.profiles if p.runtime == runtime_engine), None)
    if profile is None:
        return False
    extras = list(profile.runtime_model_ids or [])
    if name in extras:
        return False
    profile.runtime_model_ids = extras + [name]
    profile.updated_at = unix_now()
    logger.info("Entry %r: added serving name %r to %s profile from a worker row",
                entry.id, name, runtime_engine)
    return True


def _set_state(row: RuntimeModel, status: str, entry, worker_id: str) -> bool:
    """Apply (status, entry) to a row. True if anything changed."""
    catalog_id = entry.id if entry is not None else None
    transitioning = not (row.status == status and row.catalog_id == catalog_id)
    if entry is not None:
        # Hash-verified, but the picker matches on the entry's target ids.
        # A row whose local name is none of them is `available` yet would
        # never be dispatched — self-heal by extending the entry's profile
        # for the row's runtime with the name (the digest match proves the
        # artifact, the name is just what this box calls it). Checked before
        # the early return: rows are classified at birth, so a first report
        # never "transitions". A runtime the entry has no profile for is a
        # curation gap, not a heartbeat's call — warn once, on the transition.
        targets = {rmid for _rt, rmid in entry.serving_targets()}
        if row.name not in targets:
            engine = row.runtime.engine if row.runtime else None
            healed = engine is not None and _ensure_serving_name(entry, row.name, engine)
            if not healed and transitioning:
                logger.warning(
                    "Worker %s: model %r hash-matches catalogue entry %r but the "
                    "entry's runtime ids are %s — not schedulable under this "
                    "name; add a serving profile with runtime_model_id=%r",
                    worker_id, row.name, entry.id, sorted(targets), row.name,
                )
    if not transitioning:
        return False
    if status in (DRIFT, UNREGISTERED) and row.status != status:
        logger.warning(
            "Worker %s: model %r (sha256 %s) is %s — not schedulable",
            worker_id, row.name, (row.digest or "none")[:12], status,
        )
    row.status = status
    row.catalog_id = catalog_id
    row.updated_at = unix_now()
    return True


def apply_inventory(db, worker, items) -> None:
    """Upsert a worker's runtime_models rows from a full inventory report.

    `items` are InventoryItem-shaped (local_name, sha256, loaded, runtime).
    Rows are scoped per runtime: an item lands on the WorkerRuntime whose
    engine matches `item.runtime` (the worker's first runtime when unset,
    the single-runtime daemon case), and `missing` is applied only to rows
    of runtimes that appear in THIS report — a daemon only inventories its
    own runtime, so a second runtime's rows must not be marked gone every
    beat. An empty report (old daemon, runtime unreachable) changes nothing.
    """
    if not items or not worker.runtimes:
        return
    by_engine = {rt.engine: rt for rt in worker.runtimes}
    default_rt = worker.runtimes[0]
    rows = {(rt.id, m.name): m for rt in worker.runtimes for m in rt.models}
    reported_runtime_ids = set()
    seen = set()
    for item in items:
        rt = by_engine.get(getattr(item, "runtime", None)) or default_rt
        reported_runtime_ids.add(rt.id)
        key = (rt.id, item.local_name)
        seen.add(key)
        row = rows.get(key)
        if row is None:
            row = RuntimeModel(
                name=item.local_name, runtime_model_id=item.local_name,
                digest=item.sha256, loaded=item.loaded,
                details=getattr(item, "details", None),
                size_bytes=getattr(item, "size_bytes", None),
                files=_files_payload(item),
            )
            rt.models.append(row)
            rows[key] = row
        elif item.sha256 and row.digest != item.sha256:
            row.digest = item.sha256
            row.updated_at = unix_now()
        details = getattr(item, "details", None)
        if details and row.details != details:
            row.details = details
            row.updated_at = unix_now()
        size = getattr(item, "size_bytes", None)
        if size and row.size_bytes != size:
            row.size_bytes = size
        files = _files_payload(item)
        if files and row.files != files:
            row.files = files
        status, entry = classify_entry(db, item.local_name, row.digest)
        _set_state(row, status, entry, worker.id)
    for (rt_id, name), row in rows.items():
        if rt_id in reported_runtime_ids and (rt_id, name) not in seen and row.status != MISSING:
            logger.info("Worker %s: model %r no longer on disk — marked missing", worker.id, name)
            row.status = MISSING
            row.catalog_id = None
            row.loaded = False
            row.updated_at = unix_now()


def reclassify_hash(db, sha256: str) -> int:
    """Re-run classification for every worker row carrying `sha256` (after
    an adopt). Returns the number of rows that changed."""
    d = _norm(sha256)
    changed = 0
    rows = db.query(RuntimeModel).filter(
        RuntimeModel.digest.in_([d, f"sha256:{d}"])
    ).all()
    for row in rows:
        status, entry = classify_entry(db, row.name, row.digest)
        changed += _set_state(row, status, entry, row.runtime.worker_id if row.runtime else "?")
    return changed
