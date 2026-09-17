"""
Model catalogue — the curated, pinned models a user may select for a batch.

`GET /v1/models` (OpenAI-compatible surface). Returns public entries plus
any org-private entries belonging to the caller's organizations. The id
returned here is exactly what goes in a batch's `body.model`.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from models import (
    ModelCatalog, OrganizationMembership, RuntimeModel, WorkerRuntime, Worker,
)
from auth import get_human_context, require_role
from catalog_seed import validate_entry_id
from catalog_service import AdoptError, adopt
from reconciliation import DRIFT, UNREGISTERED, find_entries_by_name

router = APIRouter()


@router.get("/models")
def list_models(
    ctx=Depends(get_human_context),
    db: Session = Depends(get_db),
):
    user, _api_key = ctx

    entries = (
        db.query(ModelCatalog)
        .filter(
            ModelCatalog.enabled.is_(True),
            ModelCatalog.status.in_(ModelCatalog.SELECTABLE_STATUSES),
        )
        .order_by(ModelCatalog.id)
        .all()
    )

    # Public entries (org_id NULL) are visible to everyone; org-private
    # entries only to members of that org (superadmin sees all).
    member_org_ids = {
        m.org_id for m in db.query(OrganizationMembership).filter(
            OrganizationMembership.user_id == user.id
        ).all()
    }
    is_super = user.platform_role == "superadmin"

    data = []
    for e in entries:
        if e.org_id is not None and not is_super and e.org_id not in member_org_ids:
            continue
        data.append({
            "id": e.id,
            "object": "model",
            "display_name": e.display_name,
            "runtime": e.runtime,
            "quantization": e.quantization,
            "parameter_size": e.parameter_size,
            "context_length": e.context_length,
            "task_type": e.task_type,
            # active | unverified (adopted from a worker hash; provenance unconfirmed)
            "status": e.status,
            # NULL = seeded, 'auto' = auto-adopt pass, else adopting admin's id
            "adopted_by": e.adopted_by,
            "capabilities": e.capabilities,
            "lineage": e.lineage,
            "vram_gb": e.vram_gb,
            "size_gb": e.size_gb,
            "source_type": e.source_type,
            "source_ref": e.source_ref,
            "source_revision": e.source_revision,
            "homepage_url": e.homepage_url,
            "created_at": e.created_at,
        })

    return {"object": "list", "data": data}


# ─── Reconciliation admin surface (#116) ────────────────────

@router.get("/models/quarantine")
def list_quarantine(
    _admin=Depends(require_role("superadmin")),
    db: Session = Depends(get_db),
):
    """Worker-reported artifacts the platform will not schedule: hashes that
    match no catalogue entry (`unregistered`) and names that claim a pinned
    entry with different bytes (`drift`). Grouped by hash so one artifact on
    ten boxes is one line. Feeds the admin Models tab's Adopt / Replace."""
    rows = (
        db.query(RuntimeModel, WorkerRuntime, Worker)
        .join(WorkerRuntime, RuntimeModel.runtime_id == WorkerRuntime.id)
        .join(Worker, WorkerRuntime.worker_id == Worker.id)
        .filter(RuntimeModel.status.in_([UNREGISTERED, DRIFT]))
        .all()
    )
    groups = {}
    for m, rt, w in rows:
        # One hash can be unregistered on one box and drift on another
        # (different local names); keep the states apart.
        key = (m.digest or f"name:{m.name}", m.status)
        g = groups.setdefault(key, {
            "sha256": m.digest,
            "status": m.status,
            "local_names": set(),
            "workers": [],
            "claimed_entries": [],
            "details": None,      # first non-null runtime-reported details
        })
        g["local_names"].add(m.name)
        if g["details"] is None and m.details:
            g["details"] = m.details
        g["workers"].append({
            "worker_id": w.id, "hostname": w.hostname, "org_id": w.org_id,
            "runtime": rt.engine, "loaded": m.loaded,
        })
        if m.status == DRIFT and not g["claimed_entries"]:
            g["claimed_entries"] = [
                {"id": e.id, "digest": e.digest} for e in find_entries_by_name(db, m.name)
            ]
    data = []
    for g in groups.values():
        g["local_names"] = sorted(g["local_names"])
        data.append(g)
    return {"object": "list", "data": data}


class AdoptRequest(BaseModel):
    """Promote a quarantined worker hash into a catalogue entry."""
    sha256: str
    id: str                       # new platform slug (naming rules apply)
    display_name: str
    runtime: str                  # ollama | vllm | llamacpp
    runtime_model_id: str         # what that runtime calls it
    vram_gb: float
    quantization: Optional[str] = None
    task_type: str = "chat"
    capabilities: Optional[dict] = None
    lineage: Optional[str] = None
    size_gb: Optional[float] = None
    org_id: Optional[str] = None  # NULL = public


@router.post("/models/adopt", status_code=201)
def adopt_model(
    req: AdoptRequest,
    _admin=Depends(require_role("superadmin")),
    db: Session = Depends(get_db),
):
    """Turn "I see this file, I don't know what it is" into a catalogue entry.

    Creates the entry with status `unverified` (selectable and schedulable;
    provenance unconfirmed) plus one serving profile, then re-classifies
    every worker row carrying the hash so they flip from quarantined to
    available immediately — no YAML, no restart. Identity is the hash; the
    admin supplies what bytes cannot (vram_gb, name, capabilities).
    """
    try:
        entry, verified = adopt(
            db,
            sha256=req.sha256,
            entry_id=req.id,
            display_name=req.display_name,
            runtime=req.runtime,
            runtime_model_id=req.runtime_model_id,
            vram_gb=req.vram_gb,
            quantization=req.quantization,
            task_type=req.task_type,
            capabilities=req.capabilities,
            lineage=req.lineage,
            size_gb=req.size_gb,
            org_id=req.org_id,
            adopted_by=_admin.id,
        )
    except AdoptError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    db.commit()
    return {
        "id": entry.id,
        "status": entry.status,
        "digest": entry.digest,
        "workers_verified": verified,
    }
