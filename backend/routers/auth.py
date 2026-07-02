from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import User, ProviderCapability, PasswordResetToken, unix_now
from schemas import (
    SignupRequest, LoginRequest, ChangePasswordRequest,
    ForgotPasswordRequest, ResetPasswordRequest,
    UserOut, TokenOut,
)
from auth import (
    hash_password, verify_password, create_access_token,
    generate_api_key, get_current_user, require_role,
)
from services.email_service import send_password_reset_email
import json
import secrets
import uuid

router = APIRouter()

ALLOWED_SIGNUP_ROLES = ["user", "provider"]


@router.post("/auth/signup", response_model=UserOut)
def signup(req: SignupRequest, db: Session = Depends(get_db)):
    if req.role not in ALLOWED_SIGNUP_ROLES:
        raise HTTPException(status_code=400, detail="Role must be 'user' or 'provider'")

    existing = db.query(User).filter(User.email == req.email).first()
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")

    api_key = generate_api_key() if req.role == "user" else None

    user = User(
        email=req.email,
        password_hash=hash_password(req.password),
        full_name=req.full_name,
        role=req.role,
        api_key=api_key,
    )
    db.add(user)
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

    token = create_access_token(user.id, user.role)
    return TokenOut(
        access_token=token,
        must_change_password=user.must_change_password,
    )


@router.get("/auth/me", response_model=UserOut)
def get_profile(user=Depends(get_current_user)):
    return user


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
def regenerate_api_key(
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if user.role != "user":
        raise HTTPException(status_code=403, detail="Only users can have API keys")

    user.api_key = generate_api_key()
    db.commit()
    db.refresh(user)
    return {"api_key": user.api_key}


@router.get("/admin/users")
def list_users(
    admin=Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Admin: list all users."""
    users = db.query(User).order_by(User.created_at.desc()).all()
    return {
        "object": "list",
        "data": [
            {
                "id": u.id,
                "email": u.email,
                "full_name": u.full_name,
                "role": u.role,
                "is_active": u.is_active,
                "created_at": u.created_at,
            }
            for u in users
        ],
    }


@router.get("/admin/providers")
def list_providers(
    admin=Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Admin: list all providers and their capabilities."""
    caps = db.query(ProviderCapability).all()
    return {
        "object": "list",
        "data": [
            {
                "worker_id": c.worker_id,
                "provider_id": c.provider_id,
                "vram_total_gb": c.vram_total_gb,
                "vram_available_gb": c.vram_available_gb,
                "loaded_models": json.loads(c.loaded_models) if c.loaded_models else [],
                "status": c.status,
                "last_heartbeat": c.last_heartbeat,
            }
            for c in caps
        ],
    }


RESET_TOKEN_EXPIRY_SECONDS = 3600  # 1 hour


@router.post("/auth/forgot-password")
def forgot_password(req: ForgotPasswordRequest, db: Session = Depends(get_db)):
    """Generate reset token and send email. Always returns 200 to prevent email enumeration."""
    user = db.query(User).filter(User.email == req.email).first()
    if not user:
        return {"detail": "If that email is registered, a reset link has been sent."}

    # Invalidate any existing unused tokens for this user
    db.query(PasswordResetToken).filter(
        PasswordResetToken.user_id == user.id,
        PasswordResetToken.used == False,
    ).update({"used": True})

    # Generate new token
    token = secrets.token_urlsafe(32)
    reset_entry = PasswordResetToken(
        id=f"rst-{uuid.uuid4().hex[:24]}",
        user_id=user.id,
        token=token,
        expires_at=unix_now() + RESET_TOKEN_EXPIRY_SECONDS,
    )
    db.add(reset_entry)
    db.commit()

    # Send email via Mailgun
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
