from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import User, Organization, OrganizationMembership, ApiKey, PasswordResetToken, unix_now
from schemas import (
    SignupRequest, LoginRequest, ChangePasswordRequest,
    ForgotPasswordRequest, ResetPasswordRequest,
    UserOut, TokenOut,
)
from auth import (
    hash_password, verify_password, create_access_token,
    generate_api_key, hash_api_key, get_api_key_prefix, get_current_user, require_role,
)
from services.email_service import send_password_reset_email
import json
import secrets
import uuid

router = APIRouter()


# ─── Auth ───────────────────────────────────────────────────

@router.post("/auth/signup", response_model=UserOut)
def signup(req: SignupRequest, db: Session = Depends(get_db)):
    """Unified signup: creates user + personal org + membership."""
    existing = db.query(User).filter(User.email == req.email).first()
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")

    # Create user
    user = User(
        email=req.email,
        password_hash=hash_password(req.password),
        full_name=req.full_name,
        platform_role="user",
    )
    db.add(user)
    db.flush()

    # Auto-create personal organization
    org = Organization(
        name=f"{req.full_name}'s Personal Org",
        owner_id=user.id,
    )
    db.add(org)
    db.flush()

    # Create owner membership
    membership = OrganizationMembership(
        org_id=org.id,
        user_id=user.id,
        role="owner",
    )
    db.add(membership)

    db.commit()
    db.refresh(user)

    return user


@router.post("/auth/login", response_model=TokenOut)
def login(req: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == req.email).first()
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is disabled")

    token = create_access_token(user.id, user.platform_role)
    return TokenOut(
        access_token=token,
        platform_role=user.platform_role,
        must_change_password=user.must_change_password,
    )


@router.get("/auth/me")
def get_profile(user=Depends(get_current_user), db: Session = Depends(get_db)):
    """Return user profile with org memberships and personal API key."""
    # Reject machine identity — daemons shouldn't query this
    if getattr(user, "_is_machine_identity", False):
        raise HTTPException(status_code=403, detail="Worker keys cannot access profile endpoints")

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
            })

    # Return personal key prefix for this user
    personal_key = db.query(ApiKey).filter(
        ApiKey.created_by_user_id == user.id,
        ApiKey.key_type == "personal",
        ApiKey.status == "active",
    ).first()

    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "platform_role": user.platform_role,
        "personal_key_prefix": personal_key.key_prefix if personal_key else None,
        "is_active": user.is_active,
        "must_change_password": user.must_change_password,
        "created_at": user.created_at,
        "organizations": orgs,
    }


@router.post("/auth/change-password")
def change_password(
    req: ChangePasswordRequest,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not verify_password(req.old_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Old password is incorrect")

    user.password_hash = hash_password(req.new_password)
    user.must_change_password = False
    db.commit()
    return {"detail": "Password changed successfully"}


@router.post("/auth/api-keys/regenerate")
def regenerate_personal_api_key(
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Regenerate the user's personal API key."""
    # Reject machine identity
    if getattr(user, "_is_machine_identity", False):
        raise HTTPException(status_code=403, detail="Worker keys cannot access profile endpoints")

    api_key_entry = db.query(ApiKey).filter(
        ApiKey.created_by_user_id == user.id,
        ApiKey.key_type == "personal",
    ).first()
    if not api_key_entry:
        raise HTTPException(status_code=404, detail="No personal API key found")

    raw_key = generate_api_key()
    api_key_entry.key_prefix = get_api_key_prefix(raw_key)
    api_key_entry.key_hash = hash_api_key(raw_key)
    api_key_entry.last_used_at = None
    db.commit()
    return {"api_key": raw_key}


# ─── Superadmin ─────────────────────────────────────────────

@router.get("/admin/users")
def list_users(
    admin=Depends(require_role("superadmin")),
    db: Session = Depends(get_db),
):
    """Superadmin: list all users."""
    users = db.query(User).order_by(User.created_at.desc()).all()
    return {
        "object": "list",
        "data": [
            {
                "id": u.id,
                "email": u.email,
                "full_name": u.full_name,
                "platform_role": u.platform_role,
                "is_active": u.is_active,
                "created_at": u.created_at,
            }
            for u in users
        ],
    }


@router.get("/admin/workers")
def list_all_workers(
    admin=Depends(require_role("superadmin")),
    db: Session = Depends(get_db),
):
    """Superadmin: list all workers across all organizations."""
    workers = db.query(Worker).order_by(Worker.created_at.desc()).all()
    return {
        "object": "list",
        "data": [
            {
                "worker_id": w.id,
                "org_id": w.org_id,
                "hostname": w.hostname,
                "os": w.os,
                "cpu_cores": w.cpu_cores,
                "ram_total_gb": w.ram_total_gb,
                "gpus": json.loads(w.gpus) if w.gpus else [],
                "runtimes": json.loads(w.runtimes) if w.runtimes else [],
                "status": w.status,
                "last_heartbeat": w.last_heartbeat,
            }
            for w in workers
        ],
    }


@router.get("/admin/organizations")
def list_all_organizations(
    admin=Depends(require_role("superadmin")),
    db: Session = Depends(get_db),
):
    """Superadmin: list all organizations."""
    orgs = db.query(Organization).order_by(Organization.created_at.desc()).all()
    return {
        "object": "list",
        "data": [
            {
                "id": o.id,
                "name": o.name,
                "owner_id": o.owner_id,
                "created_at": o.created_at,
            }
            for o in orgs
        ],
    }


# ─── Password Reset ────────────────────────────────────────

RESET_TOKEN_EXPIRY_SECONDS = 3600  # 1 hour


@router.post("/auth/forgot-password")
def forgot_password(req: ForgotPasswordRequest, db: Session = Depends(get_db)):
    """Generate reset token and send email. Always returns 200 to prevent email enumeration."""
    user = db.query(User).filter(User.email == req.email).first()
    if not user:
        return {"detail": "If that email is registered, a reset link has been sent."}

    db.query(PasswordResetToken).filter(
        PasswordResetToken.user_id == user.id,
        PasswordResetToken.used == False,
    ).update({"used": True})

    token = secrets.token_urlsafe(32)
    reset_entry = PasswordResetToken(
        id=f"rst-{uuid.uuid4().hex[:24]}",
        user_id=user.id,
        token=token,
        expires_at=unix_now() + RESET_TOKEN_EXPIRY_SECONDS,
    )
    db.add(reset_entry)
    db.commit()

    send_password_reset_email(user.email, token)

    return {"detail": "If that email is registered, a reset link has been sent."}


@router.post("/auth/reset-password")
def reset_password(req: ResetPasswordRequest, db: Session = Depends(get_db)):
    """Validate reset token and set new password."""
    reset_entry = db.query(PasswordResetToken).filter(
        PasswordResetToken.token == req.token,
        PasswordResetToken.used == False,
    ).first()

    if not reset_entry:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    if reset_entry.expires_at < unix_now():
        reset_entry.used = True
        db.commit()
        raise HTTPException(status_code=400, detail="Reset token has expired")

    user = db.query(User).filter(User.id == reset_entry.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.password_hash = hash_password(req.new_password)
    user.must_change_password = False
    reset_entry.used = True
    db.commit()

    return {"detail": "Password has been reset successfully. You can now log in."}
