"""
Model catalogue — the curated, pinned models a user may select for a batch.

`GET /v1/models` (OpenAI-compatible surface). Returns public entries plus
any org-private entries belonging to the caller's organizations. The id
returned here is exactly what goes in a batch's `body.model`.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from database import get_db
from models import ModelCatalog, OrganizationMembership
from auth import get_human_context

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
            ModelCatalog.status == "active",
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
            "vram_gb": e.vram_gb,
            "size_gb": e.size_gb,
            "source_type": e.source_type,
            "source_ref": e.source_ref,
            "source_revision": e.source_revision,
            "homepage_url": e.homepage_url,
            "created_at": e.created_at,
        })

    return {"object": "list", "data": data}
