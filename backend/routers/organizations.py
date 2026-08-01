"""
Organization-scoped endpoints — dashboard-facing, JWT-authenticated.

Issue #16: Full membership management, invites, self-service, API key CRUD.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr
from typing import Optional
from database import get_db
from models import (
    ApiKey, Organization, OrganizationMembership, OrganizationInvite,
    Worker, WorkerRuntime, RuntimeModel, WorkerGpu,
    Batch, BatchAssignment, User, get_org_owner, unix_now,
    generate_org_id, generate_membership_id,
)
from auth import (
    get_current_user, generate_api_key, hash_api_key, get_api_key_prefix,
)
import secrets

router = APIRouter()

INVITE_EXPIRY_SECONDS = 7 * 24 * 3600  # 7 days


# ─── Request Schemas ────────────────────────────────────────

class InviteRequest(BaseModel):
    email: str
    role: str = "viewer"   # "owner" | "admin" | "viewer"

class RoleUpdateRequest(BaseModel):
    role: str              # "owner" | "admin" | "viewer"

class CreateOrgRequest(BaseModel):
    name: str

class TransferOwnerRequest(BaseModel):
    new_owner_user_id: str

class CreateApiKeyRequest(BaseModel):
    name: str = "Worker Key"
    key_type: str = "worker"   # "worker" or "personal"


# ─── Helpers ────────────────────────────────────────────────

VALID_ROLES = {"owner", "admin", "viewer"}
VALID_KEY_TYPES = {"worker", "personal"}


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


def _validate_role(role: str):
    if role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail=f"Invalid role '{role}'. Must be one of: {', '.join(VALID_ROLES)}")


def _count_owners(db: Session, org_id: str) -> int:
    return db.query(OrganizationMembership).filter(
        OrganizationMembership.org_id == org_id,
        OrganizationMembership.role == "owner",
    ).count()


# ─── Existing: List user orgs, API keys, Workers ───────────

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
    """List API keys for an organization. Must be a member."""
    _require_membership(db, user, org_id)

    keys = db.query(ApiKey).filter(
        ApiKey.org_id == org_id,
    ).order_by(ApiKey.created_at.desc()).all()
    return {
        "object": "list",
        "data": [
            {
                "id": k.id,
                "key_prefix": k.key_prefix,
                "name": k.name,
                "key_type": k.key_type,
                "status": k.status,
                "last_used_at": k.last_used_at,
                "expires_at": k.expires_at,
                "created_at": k.created_at,
            }
            for k in keys
        ],
    }


@router.post("/orgs/{org_id}/api-keys")
def create_api_key(
    org_id: str,
    req: CreateApiKeyRequest,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a new API key for an organization. Owner/Admin only.
    Returns the raw key ONCE — only the hash is persisted.
    """
    _require_membership(db, user, org_id, roles=["owner", "admin"])

    if req.key_type not in VALID_KEY_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid key_type '{req.key_type}'. Must be one of: {', '.join(VALID_KEY_TYPES)}",
        )

    raw_key = generate_api_key()
    api_key = ApiKey(
        org_id=org_id,
        key_type=req.key_type,
        created_by_user_id=user.id,
        name=req.name,
        key_prefix=get_api_key_prefix(raw_key),
        key_hash=hash_api_key(raw_key),
    )
    db.add(api_key)
    db.commit()
    db.refresh(api_key)

    return {
        "id": api_key.id,
        "api_key": raw_key,
        "key_prefix": api_key.key_prefix,
        "name": api_key.name,
        "key_type": api_key.key_type,
        "org_id": org_id,
        "message": "Save this key now. It cannot be retrieved later.",
    }


@router.delete("/orgs/{org_id}/api-keys/{key_id}")
def revoke_api_key(
    org_id: str,
    key_id: str,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Revoke (deactivate) an API key. Owner/Admin only."""
    _require_membership(db, user, org_id, roles=["owner", "admin"])

    api_key = db.query(ApiKey).filter(
        ApiKey.id == key_id,
        ApiKey.org_id == org_id,
    ).first()
    if not api_key:
        raise HTTPException(status_code=404, detail="API key not found")

    api_key.status = "revoked"
    api_key.revoked_at = unix_now()
    db.commit()
    return {"detail": "API key revoked", "id": key_id}


@router.post("/orgs/{org_id}/api-keys/regenerate")
def regenerate_org_api_key(
    org_id: str,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Regenerate a worker API key for an organization. Owner/Admin only."""
    _require_membership(db, user, org_id, roles=["owner", "admin"])

    api_key_entry = db.query(ApiKey).filter(
        ApiKey.org_id == org_id,
        ApiKey.key_type == "worker",
        ApiKey.status == "active",
    ).first()
    if not api_key_entry:
        raise HTTPException(status_code=404, detail="No active worker API key found for this org")

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
                "gpus": [
                    {
                        "index": g.gpu_index, "vendor": g.vendor, "name": g.name,
                        "vram_gb": g.vram_gb, "driver": g.driver, "cuda": g.cuda,
                    }
                    for g in w.gpus
                ],
                "runtimes": [
                    {
                        "type": rt.engine, "endpoint": rt.base_url,
                        "models": [m.name for m in rt.models],
                    }
                    for rt in w.runtimes
                ],
                "status": w.status,
                "activity": w.activity,
                "vram_total_gb": w.vram_total_gb,
                "vram_available_gb": w.vram_available_gb,
                "loaded_models": w.loaded_model_names(),
                "last_heartbeat": w.last_heartbeat,
                "created_at": w.created_at,
            }
            for w in workers
        ],
    }


# ═══════════════════════════════════════════════════════════
# PROVIDER PORTAL (spec §15 — expanded design 2026-08-01)
# ═══════════════════════════════════════════════════════════

class OrgRenameRequest(BaseModel):
    name: str


@router.put("/orgs/{org_id}")
def rename_org(
    org_id: str,
    req: OrgRenameRequest,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Rename an organization. Owner/admin only."""
    _require_membership(db, user, org_id, roles=["owner", "admin"])

    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Organization name cannot be empty")

    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    org.name = name
    db.commit()
    return {"id": org.id, "name": org.name}


@router.get("/orgs/{org_id}/batches")
def list_org_served_batches(
    org_id: str,
    limit: int = 100,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Batches served by this org's workers — provider view (spec §15).

    BatchSummary shape only: metadata, never input files or prompt content.
    """
    _require_membership(db, user, org_id)

    rows = (
        db.query(Batch, BatchAssignment, Worker)
        .join(BatchAssignment, BatchAssignment.batch_id == Batch.id)
        .join(Worker, Worker.id == BatchAssignment.worker_id)
        .filter(Worker.org_id == org_id)
        .order_by(BatchAssignment.assigned_at.desc())
        .limit(max(1, min(limit, 500)))
        .all()
    )
    return {
        "object": "list",
        "data": [
            {
                "id": b.id,
                "object": "batch",
                "endpoint": b.endpoint,
                "model": b.model,
                "status": b.status,
                "created_at": b.created_at,
                "completed_at": b.completed_at,
                "request_counts": {
                    "total": b.request_counts_total or 0,
                    "completed": b.request_counts_completed or 0,
                    "failed": b.request_counts_failed or 0,
                },
                "worker_id": w.id,
                "worker_hostname": w.hostname,
                "assigned_at": a.assigned_at,
            }
            for b, a, w in rows
        ],
    }


@router.get("/orgs/{org_id}/stats")
def org_served_stats(
    org_id: str,
    days: int = 7,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Contribution aggregates for the provider portal (spec §15).

    Counts requests served (no token accounting on batches yet — that
    lands with real usage metering, §16).
    """
    _require_membership(db, user, org_id)

    since = unix_now() - max(1, min(days, 365)) * 86400
    rows = (
        db.query(Batch, BatchAssignment, Worker)
        .join(BatchAssignment, BatchAssignment.batch_id == Batch.id)
        .join(Worker, Worker.id == BatchAssignment.worker_id)
        .filter(Worker.org_id == org_id, BatchAssignment.assigned_at >= since)
        .all()
    )

    totals = {"jobs": 0, "completed": 0, "failed": 0, "in_progress": 0, "requests_completed": 0}
    by_model, by_worker = {}, {}
    for b, _a, w in rows:
        totals["jobs"] += 1
        if b.status in totals:
            totals[b.status] += 1
        reqs = b.request_counts_completed or 0
        totals["requests_completed"] += reqs

        m = by_model.setdefault(b.model or "unknown", {"jobs": 0, "requests_completed": 0})
        m["jobs"] += 1
        m["requests_completed"] += reqs

        wk = by_worker.setdefault(w.id, {"hostname": w.hostname, "jobs": 0, "requests_completed": 0})
        wk["jobs"] += 1
        wk["requests_completed"] += reqs

    return {
        "window_days": days,
        "totals": totals,
        "by_model": [{"model": k, **v} for k, v in sorted(by_model.items(), key=lambda kv: -kv[1]["requests_completed"])],
        "by_worker": [{"worker_id": k, **v} for k, v in sorted(by_worker.items(), key=lambda kv: -kv[1]["requests_completed"])],
    }


@router.post("/orgs/{org_id}/workers/{worker_id}/drain")
def drain_worker(
    org_id: str,
    worker_id: str,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Stop routing new jobs to a worker; it finishes its current batch.

    Owner/admin only. Heartbeats preserve the draining state; the sweeper
    still marks a silent draining worker offline.
    """
    _require_membership(db, user, org_id, roles=["owner", "admin"])

    worker = db.query(Worker).filter(Worker.id == worker_id, Worker.org_id == org_id).first()
    if not worker:
        raise HTTPException(status_code=404, detail="Worker not found in this organization")
    if worker.status == "offline":
        raise HTTPException(status_code=409, detail="Worker is offline; nothing to drain")

    worker.status = "draining"
    db.commit()
    return {"id": worker.id, "status": worker.status}


@router.post("/orgs/{org_id}/workers/{worker_id}/undrain")
def undrain_worker(
    org_id: str,
    worker_id: str,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return a draining worker to the pool. Owner/admin only."""
    _require_membership(db, user, org_id, roles=["owner", "admin"])

    worker = db.query(Worker).filter(Worker.id == worker_id, Worker.org_id == org_id).first()
    if not worker:
        raise HTTPException(status_code=404, detail="Worker not found in this organization")
    if worker.status != "draining":
        raise HTTPException(status_code=409, detail=f"Worker is {worker.status}, not draining")

    worker.status = "online"
    db.commit()
    return {"id": worker.id, "status": worker.status}


@router.delete("/orgs/{org_id}/workers/{worker_id}")
def remove_worker(
    org_id: str,
    worker_id: str,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Remove an offline worker and its inventory. Owner/admin only.

    Batch assignment history is kept (the batches themselves are the
    record); the served-jobs join simply stops resolving the hostname.
    """
    _require_membership(db, user, org_id, roles=["owner", "admin"])

    worker = db.query(Worker).filter(Worker.id == worker_id, Worker.org_id == org_id).first()
    if not worker:
        raise HTTPException(status_code=404, detail="Worker not found in this organization")
    if worker.status != "offline":
        raise HTTPException(
            status_code=409,
            detail=f"Worker is {worker.status}; drain it and wait for offline before removing",
        )

    runtime_ids = [rt.id for rt in worker.runtimes]
    if runtime_ids:
        db.query(RuntimeModel).filter(RuntimeModel.runtime_id.in_(runtime_ids)).delete(synchronize_session=False)
        db.query(WorkerRuntime).filter(WorkerRuntime.id.in_(runtime_ids)).delete(synchronize_session=False)
    db.query(WorkerGpu).filter(WorkerGpu.worker_id == worker.id).delete(synchronize_session=False)
    db.delete(worker)
    db.commit()
    return {"id": worker_id, "deleted": True}


# ═══════════════════════════════════════════════════════════
# MEMBERSHIP MANAGEMENT (Issue #16)
# ═══════════════════════════════════════════════════════════

# ─── List members ───────────────────────────────────────────

@router.get("/orgs/{org_id}/members")
def list_members(
    org_id: str,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List all members of an organization. Any member can view."""
    _require_membership(db, user, org_id)

    memberships = (
        db.query(OrganizationMembership, User)
        .join(User, User.id == OrganizationMembership.user_id)
        .filter(OrganizationMembership.org_id == org_id)
        .all()
    )

    return {
        "object": "list",
        "data": [
            {
                "user_id": u.id,
                "email": u.email,
                "full_name": u.full_name,
                "role": m.role,
                "membership_id": m.id,
                "joined_at": m.created_at,
            }
            for m, u in memberships
        ],
    }


# ─── Invite a user ─────────────────────────────────────────

@router.post("/orgs/{org_id}/members/invite")
def invite_member(
    org_id: str,
    req: InviteRequest,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Invite a user to the org by email. Owner/Admin only.
    If the user exists, creates the membership directly.
    If not, creates a tokenized invite for later acceptance.
    """
    _require_membership(db, user, org_id, roles=["owner", "admin"])
    _validate_role(req.role)

    # FIX #1: Prevent privilege escalation — only owners can confer owner role
    if req.role == "owner":
        _require_membership(db, user, org_id, roles=["owner"])

    # FIX #4: Case-fold email for consistent matching
    req.email = req.email.strip().lower()

    # Check if already a member
    existing_user = db.query(User).filter(User.email == req.email).first()
    if existing_user:
        existing_membership = db.query(OrganizationMembership).filter(
            OrganizationMembership.org_id == org_id,
            OrganizationMembership.user_id == existing_user.id,
        ).first()
        if existing_membership:
            raise HTTPException(status_code=409, detail="User is already a member of this org")

        # User exists — create membership directly
        membership = OrganizationMembership(
            org_id=org_id,
            user_id=existing_user.id,
            role=req.role,
        )
        db.add(membership)
        db.commit()
        return {
            "status": "member_added",
            "user_id": existing_user.id,
            "email": req.email,
            "role": req.role,
            "message": f"{req.email} added as {req.role}",
        }

    # User doesn't exist — create invite token
    # Check for existing pending invite
    existing_invite = db.query(OrganizationInvite).filter(
        OrganizationInvite.org_id == org_id,
        OrganizationInvite.invitee_email == req.email,
        OrganizationInvite.accepted == False,
        OrganizationInvite.expires_at > unix_now(),
    ).first()
    if existing_invite:
        raise HTTPException(status_code=409, detail="A pending invite already exists for this email")

    token = secrets.token_urlsafe(32)
    invite = OrganizationInvite(
        org_id=org_id,
        inviter_id=user.id,
        invitee_email=req.email,
        role=req.role,
        token=token,
        expires_at=unix_now() + INVITE_EXPIRY_SECONDS,
    )
    db.add(invite)
    db.commit()
    db.refresh(invite)

    # Optionally send email
    try:
        from services.email_service import send_email
        import os
        frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000")
        join_link = f"{frontend_url}/join?token={token}"
        org = db.query(Organization).filter(Organization.id == org_id).first()
        org_name = org.name if org else "an organization"
        send_email(
            req.email,
            f"You're invited to join {org_name} on Moonknight",
            f"{user.full_name} invited you to join '{org_name}' as {req.role}.\n\n"
            f"Click here to accept: {join_link}\n\n"
            f"This invite expires in 7 days.\n"
            f"If you don't have an account, sign up first, then use the link above.",
        )
    except Exception:
        pass  # Email is best-effort

    return {
        "status": "invite_sent",
        "invite_id": invite.id,
        "token": token,
        "email": req.email,
        "role": req.role,
        "expires_at": invite.expires_at,
        "message": f"Invite sent to {req.email}",
    }


# ─── List pending invites ──────────────────────────────────

@router.get("/orgs/{org_id}/members/invites")
def list_invites(
    org_id: str,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List pending invites for an organization. Owner/Admin only."""
    _require_membership(db, user, org_id, roles=["owner", "admin"])

    invites = db.query(OrganizationInvite).filter(
        OrganizationInvite.org_id == org_id,
        OrganizationInvite.accepted == False,
    ).order_by(OrganizationInvite.created_at.desc()).all()

    return {
        "object": "list",
        "data": [
            {
                "id": i.id,
                "email": i.invitee_email,
                "role": i.role,
                "token": i.token,
                "inviter_id": i.inviter_id,
                "expires_at": i.expires_at,
                "expired": i.expires_at < unix_now(),
                "created_at": i.created_at,
            }
            for i in invites
        ],
    }


# ─── Revoke an invite ──────────────────────────────────────

@router.delete("/orgs/{org_id}/members/invites/{invite_token}")
def revoke_invite(
    org_id: str,
    invite_token: str,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Revoke a pending invite. Owner/Admin only."""
    _require_membership(db, user, org_id, roles=["owner", "admin"])

    invite = db.query(OrganizationInvite).filter(
        OrganizationInvite.org_id == org_id,
        OrganizationInvite.token == invite_token,
        OrganizationInvite.accepted == False,
    ).first()
    if not invite:
        raise HTTPException(status_code=404, detail="Invite not found or already accepted")

    db.delete(invite)
    db.commit()
    return {"detail": "Invite revoked"}


# ─── Leave org (self-service) ──────────────────────────────
# NOTE: Must be registered BEFORE /{user_id} routes to avoid
# FastAPI matching "me" as a user_id path parameter.

@router.delete("/orgs/{org_id}/members/me")
def leave_organization(
    org_id: str,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Leave an organization. Fails if the caller is the sole Owner."""
    membership = db.query(OrganizationMembership).filter(
        OrganizationMembership.org_id == org_id,
        OrganizationMembership.user_id == user.id,
    ).first()
    if not membership:
        raise HTTPException(status_code=404, detail="You are not a member of this org")

    if membership.role == "owner" and _count_owners(db, org_id) <= 1:
        raise HTTPException(
            status_code=409,
            detail="Cannot leave as sole owner. Transfer ownership first.",
        )

    db.delete(membership)
    db.commit()
    return {"detail": "You have left the organization"}


@router.post("/orgs/{org_id}/members/transfer-owner")
def transfer_ownership(
    org_id: str,
    req: TransferOwnerRequest,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Transfer ownership to another member. Caller becomes admin."""
    caller_membership = _require_membership(db, user, org_id, roles=["owner"])

    # FIX #2: Guard self-transfer — SQLAlchemy identity map would make both
    # references point to the same row, ending up with role="admin" and 0 owners.
    if req.new_owner_user_id == user.id:
        raise HTTPException(status_code=400, detail="You are already the owner")

    target_membership = db.query(OrganizationMembership).filter(
        OrganizationMembership.org_id == org_id,
        OrganizationMembership.user_id == req.new_owner_user_id,
    ).first()
    if not target_membership:
        raise HTTPException(status_code=404, detail="Target user is not a member of this org")

    # Transfer
    target_membership.role = "owner"
    caller_membership.role = "admin"
    db.commit()

    return {
        "detail": "Ownership transferred",
        "new_owner": req.new_owner_user_id,
        "caller_new_role": "admin",
    }


# ─── Update member role ────────────────────────────────────

@router.put("/orgs/{org_id}/members/{user_id}")
def update_member_role(
    org_id: str,
    user_id: str,
    req: RoleUpdateRequest,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update a member's role. Owner only."""
    _require_membership(db, user, org_id, roles=["owner"])
    _validate_role(req.role)

    membership = db.query(OrganizationMembership).filter(
        OrganizationMembership.org_id == org_id,
        OrganizationMembership.user_id == user_id,
    ).first()
    if not membership:
        raise HTTPException(status_code=404, detail="Member not found")

    # Cannot demote the sole owner
    if membership.role == "owner" and req.role != "owner":
        if _count_owners(db, org_id) <= 1:
            raise HTTPException(status_code=409, detail="Cannot demote the sole owner. Transfer ownership first.")

    membership.role = req.role
    db.commit()
    return {"detail": f"Role updated to {req.role}", "user_id": user_id}


# ─── Remove a member ──────────────────────────────────────

@router.delete("/orgs/{org_id}/members/{user_id}")
def remove_member(
    org_id: str,
    user_id: str,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Remove a member from the org. Owner/Admin only. Cannot remove the sole owner."""
    _require_membership(db, user, org_id, roles=["owner", "admin"])

    membership = db.query(OrganizationMembership).filter(
        OrganizationMembership.org_id == org_id,
        OrganizationMembership.user_id == user_id,
    ).first()
    if not membership:
        raise HTTPException(status_code=404, detail="Member not found")

    if membership.role == "owner" and _count_owners(db, org_id) <= 1:
        raise HTTPException(status_code=409, detail="Cannot remove the sole owner")

    # Admin cannot remove owner
    caller_membership = db.query(OrganizationMembership).filter(
        OrganizationMembership.org_id == org_id,
        OrganizationMembership.user_id == user.id,
    ).first()
    if caller_membership and caller_membership.role == "admin" and membership.role == "owner":
        raise HTTPException(status_code=403, detail="Admin cannot remove an owner")

    db.delete(membership)
    db.commit()
    return {"detail": "Member removed", "user_id": user_id}


# ═══════════════════════════════════════════════════════════
# USER SELF-SERVICE
# ═══════════════════════════════════════════════════════════

@router.get("/me/organizations")
def my_organizations(
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List orgs the current user belongs to (alias of GET /orgs)."""
    memberships = (
        db.query(OrganizationMembership, Organization)
        .join(Organization, Organization.id == OrganizationMembership.org_id)
        .filter(OrganizationMembership.user_id == user.id)
        .all()
    )
    return {
        "object": "list",
        "data": [
            {
                "id": org.id,
                "name": org.name,
                "role": m.role,
                "derived_owner_id": get_org_owner(db, org.id),
                "created_at": org.created_at,
            }
            for m, org in memberships
        ],
    }


@router.post("/me/organizations")
def create_organization(
    req: CreateOrgRequest,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a new org. The caller becomes the Owner."""
    org = Organization(name=req.name)
    db.add(org)
    db.flush()

    membership = OrganizationMembership(
        org_id=org.id,
        user_id=user.id,
        role="owner",
    )
    db.add(membership)
    db.commit()
    db.refresh(org)

    return {
        "id": org.id,
        "name": org.name,
        "role": "owner",
        "created_at": org.created_at,
    }





# ═══════════════════════════════════════════════════════════
# INVITE ACCEPTANCE (no membership required)
# ═══════════════════════════════════════════════════════════

@router.post("/orgs/join/{invite_token}")
def accept_invite(
    invite_token: str,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Accept an invite and join the organization."""
    invite = db.query(OrganizationInvite).filter(
        OrganizationInvite.token == invite_token,
        OrganizationInvite.accepted == False,
    ).first()
    if not invite:
        raise HTTPException(status_code=404, detail="Invite not found or already accepted")

    if invite.expires_at < unix_now():
        raise HTTPException(status_code=410, detail="Invite has expired")

    # FIX #4: Case-insensitive email comparison
    if invite.invitee_email.lower() != user.email.lower():
        raise HTTPException(status_code=403, detail="This invite is for a different email address")

    # Check not already a member
    existing = db.query(OrganizationMembership).filter(
        OrganizationMembership.org_id == invite.org_id,
        OrganizationMembership.user_id == user.id,
    ).first()
    if existing:
        invite.accepted = True
        db.commit()
        raise HTTPException(status_code=409, detail="You are already a member of this org")

    # Create membership
    membership = OrganizationMembership(
        org_id=invite.org_id,
        user_id=user.id,
        role=invite.role,
    )
    db.add(membership)
    invite.accepted = True
    invite.invitee_user_id = user.id
    db.commit()

    org = db.query(Organization).filter(Organization.id == invite.org_id).first()
    return {
        "detail": "Invite accepted",
        "org_id": invite.org_id,
        "org_name": org.name if org else None,
        "role": invite.role,
    }


@router.get("/me/invites")
def my_invites(
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List invites the current user has received (by email)."""
    invites = (
        db.query(OrganizationInvite, Organization)
        .join(Organization, Organization.id == OrganizationInvite.org_id)
        .filter(OrganizationInvite.invitee_email == user.email)
        .order_by(OrganizationInvite.created_at.desc())
        .all()
    )

    return {
        "object": "list",
        "data": [
            {
                "id": i.id,
                "org_id": i.org_id,
                "org_name": org.name,
                "role": i.role,
                "token": i.token,
                "accepted": i.accepted,
                "expired": i.expires_at < unix_now(),
                "expires_at": i.expires_at,
                "created_at": i.created_at,
            }
            for i, org in invites
        ],
    }
