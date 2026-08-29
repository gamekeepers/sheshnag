"""
Pool capacity — what the shared pool looks like right now, in aggregate.

Batch users can see their batch's status but nothing about the pool
running it. This endpoint answers "is anyone even online, and can they
run my model?" without disclosing *whose* machine is whose: on a campus
deployment a hostname or a GPU name identifies a colleague, so nothing
per-worker is ever serialized here (that view stays on the superadmin
`/v1/admin/workers`).

Public by design — the deployment is on-prem — but the anonymous view is
deliberately coarser than the authenticated one, and the whole thing is
served from a short TTL cache so an unauthenticated caller cannot turn
repeated requests into repeated database scans.
"""
import threading
import time

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session, joinedload

from auth import get_optional_human_context
from database import get_db
from models import (
    ModelCatalog,
    OrganizationMembership,
    Worker,
    WorkerRuntime,
    unix_now,
)
from provider_picker import can_serve
from sweeper import HEARTBEAT_TIMEOUT_SECONDS

router = APIRouter()

# The snapshot changes no faster than the 30s heartbeat, so a short cache
# costs accuracy nothing and makes the cost of a request constant.
CACHE_TTL_SECONDS = 15

# Below this many online workers, "312 GB of VRAM" stops being an
# aggregate and starts naming whose machine is online. Suppress the
# figure rather than the whole strip.
MIN_WORKERS_FOR_VRAM = 3

_cache_lock = threading.Lock()
_cache = {"expires_at": 0.0, "snapshot": None}


def _compute_snapshot(db: Session) -> dict:
    """Everything the endpoint can know, before per-caller filtering."""
    # The sweeper marks stale workers offline only once a minute, so a
    # row can read "online" for up to ~3 minutes after the daemon died.
    # Apply the heartbeat cutoff here too — this is a liveness display.
    cutoff = unix_now() - HEARTBEAT_TIMEOUT_SECONDS
    workers = (
        db.query(Worker)
        .options(
            joinedload(Worker.runtimes).joinedload(WorkerRuntime.models),
        )
        .filter(
            Worker.status == "online",
            Worker.last_heartbeat >= cutoff,
        )
        .all()
    )

    entries = (
        db.query(ModelCatalog)
        .filter(
            ModelCatalog.enabled.is_(True),
            ModelCatalog.status == "active",
        )
        .order_by(ModelCatalog.id)
        .all()
    )

    idle = sum(1 for w in workers if w.activity == "idle")
    vram = sum(w.vram_total_gb or 0 for w in workers)

    # A model is servable when at least one online worker could be given
    # a batch for it — the scheduler's own predicate, not a lookalike.
    advertised = [(w.advertised_models(), w.vram_total_gb) for w in workers]
    servable = [
        {
            "id": e.id,
            "display_name": e.display_name,
            "parameter_size": e.parameter_size,
            "org_id": e.org_id,
        }
        for e in entries
        if any(can_serve(e, models, w_vram) for models, w_vram in advertised)
    ]

    return {
        "workers_online": len(workers),
        "workers_idle": idle,
        "workers_busy": len(workers) - idle,
        "vram_total_gb": round(vram, 1) if vram else 0.0,
        "servable": servable,
        "as_of": unix_now(),
    }


def _get_snapshot(db: Session) -> dict:
    now = time.monotonic()
    with _cache_lock:
        if _cache["snapshot"] is not None and now < _cache["expires_at"]:
            return _cache["snapshot"]
    snapshot = _compute_snapshot(db)
    with _cache_lock:
        _cache["snapshot"] = snapshot
        _cache["expires_at"] = time.monotonic() + CACHE_TTL_SECONDS
    return snapshot


def _reset_cache():
    """Drop the cached snapshot (tests; not part of the API)."""
    with _cache_lock:
        _cache["snapshot"] = None
        _cache["expires_at"] = 0.0


def _visible_org_ids(db: Session, user) -> tuple:
    """(member org ids, is_superadmin) for catalogue visibility."""
    if user is None:
        return set(), False
    org_ids = {
        m.org_id
        for m in db.query(OrganizationMembership)
        .filter(OrganizationMembership.user_id == user.id)
        .all()
    }
    return org_ids, user.platform_role in ("superadmin", "admin")


@router.get("/pool/capacity")
def pool_capacity(
    ctx=Depends(get_optional_human_context),
    db: Session = Depends(get_db),
):
    """Aggregate live capacity of the pool. Auth optional.

    Anonymous callers get worker counts and the publicly servable models.
    Authenticated callers additionally get total VRAM (suppressed while
    the pool is thin enough for the figure to identify a machine) and any
    org-private catalogue entries they can actually select.

    Never returns hostnames, GPU names, per-worker rows, or org ids.
    """
    user = ctx[0] if ctx else None
    snapshot = _get_snapshot(db)
    member_org_ids, is_super = _visible_org_ids(db, user)

    models_servable = [
        {
            "id": m["id"],
            "display_name": m["display_name"],
            "parameter_size": m["parameter_size"],
        }
        for m in snapshot["servable"]
        if m["org_id"] is None or is_super or m["org_id"] in member_org_ids
    ]

    body = {
        "object": "pool.capacity",
        "workers_online": snapshot["workers_online"],
        "workers_idle": snapshot["workers_idle"],
        "workers_busy": snapshot["workers_busy"],
        "models_servable": models_servable,
        "as_of": snapshot["as_of"],
    }

    if user is not None and snapshot["workers_online"] >= MIN_WORKERS_FOR_VRAM:
        body["vram_total_gb"] = snapshot["vram_total_gb"]
    else:
        body["vram_total_gb"] = None

    return body
