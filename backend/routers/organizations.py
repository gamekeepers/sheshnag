"""
Organization-scoped endpoints — dashboard-facing, JWT-authenticated.

Moved out of the workers router: these used to be reachable only under
/workers/v1/orgs/… because they were defined inside the router
mounted at /workers. They now live at /v1/orgs/… .
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import (
    ApiKey, Organization, OrganizationMembership, Worker,
    get_org_owner,
)
from auth import (
    get_current_user, generate_api_key, hash_api_key, get_api_key_prefix,
)
import json

router = APIRouter()


def _require_membership(db: Session, user, org_id: str, roles: list | None = None):
    """403 unless the user is a member of the org (optionally with a role)."""
    query = db.query(OrganizationMembership).filter(
        OrganizationMembership.org_id == org_id,
        OrganizationMembership.user_id == user.id,
    )
    if roles:
        query = query.filter(OrganizationMembership.role.in_(roles))
    membership = query.first()

    if not membership and user.platform_role != "superadmin":
        detail = (
            "Only org owner/admin can perform this action"
            if roles else "Not a member of this organization"
        )
        raise HTTPException(status_code=403, detail=detail)
    return membership


@router.get("/orgs")
def list_user_organizations(
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List organizations the current user belongs to."""
    memberships = db.query(OrganizationMembership).filter(
        OrganizationMembership.user_id == user.id
    ).all()

    orgs = []
    for m in memberships:
        org = db.query(Organization).filter(Organization.id == m.org_id).first()
        if org:
            orgs.append({
                "id": org.id,
                "name": org.name,
                "role": m.role,
                "derived_owner_id": get_org_owner(db, org.id),
                "created_at": org.created_at,
            })

    return {"object": "list", "data": orgs}


@router.get("/orgs/{org_id}/api-keys")
def list_org_api_keys(
    org_id: str,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List worker API keys for an organization. Must be a member."""
    _require_membership(db, user, org_id)

    keys = db.query(ApiKey).filter(
        ApiKey.org_id == org_id,
        ApiKey.key_type == "worker",
    ).order_by(ApiKey.created_at.desc()).all()
    return {
        "object": "list",
        "data": [
            {
                "id": k.id,
                "key_prefix": k.key_prefix,
                "name": k.name,
                "status": k.status,
                "last_used_at": k.last_used_at,
                "expires_at": k.expires_at,
                "created_at": k.created_at,
            }
            for k in keys
        ],
    }


@router.post("/orgs/{org_id}/api-keys/regenerate")
def regenerate_org_api_key(
    org_id: str,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Regenerate a worker API key for an organization. Must be owner or admin."""
    _require_membership(db, user, org_id, roles=["owner", "admin"])

    api_key_entry = db.query(ApiKey).filter(
        ApiKey.org_id == org_id,
        ApiKey.key_type == "worker",
    ).first()
    if not api_key_entry:
        raise HTTPException(status_code=404, detail="No worker API key found for this org")

    raw_key = generate_api_key()
    api_key_entry.key_prefix = get_api_key_prefix(raw_key)
    api_key_entry.key_hash = hash_api_key(raw_key)
    db.commit()
    db.refresh(api_key_entry)
    return {"api_key": raw_key}


@router.get("/orgs/{org_id}/workers")
def list_org_workers(
    org_id: str,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List workers belonging to an organization. Must be a member."""
    _require_membership(db, user, org_id)

    workers = (
        db.query(Worker)
        .filter(Worker.org_id == org_id)
        .order_by(Worker.created_at.desc())
        .all()
    )
    return {
        "object": "list",
        "data": [
            {
                "id": w.id,
                "hostname": w.hostname,
                "os": w.os,
                "cpu_cores": w.cpu_cores,
                "ram_total_gb": w.ram_total_gb,
                "gpus": json.loads(w.gpus) if w.gpus else [],
                "runtimes": json.loads(w.runtimes) if w.runtimes else [],
                "status": w.status,
                "activity": w.activity,
                "vram_total_gb": w.vram_total_gb,
                "vram_available_gb": w.vram_available_gb,
                "loaded_models": json.loads(w.loaded_models) if w.loaded_models else [],
                "last_heartbeat": w.last_heartbeat,
                "created_at": w.created_at,
            }
            for w in workers
        ],
    }
