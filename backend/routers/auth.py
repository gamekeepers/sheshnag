from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from database import get_db
from models import (
    User, Organization, OrganizationMembership, ApiKey, Worker,
    PasswordResetToken, AllowedEmailDomain, unix_now, get_org_owner,
)
from schemas import (
    SignupRequest, LoginRequest, ChangePasswordRequest,
    ForgotPasswordRequest, ResetPasswordRequest,
    GoogleAuthRequest, GoogleTokenOut,
    AllowedDomainCreate,
    UserOut, TokenOut,
)
from auth import (
    hash_password, verify_password, create_access_token,
    generate_api_key, hash_api_key, get_api_key_prefix,
    get_current_user, get_human_context, require_role,
    verify_google_token,
)
# get_current_user is JWT-only
# get_human_context accepts JWT or personal API key
from services.email_service import send_password_reset_email
import secrets
import uuid

router = APIRouter()


def normalize_email(email: str) -> str:
    """One canonical form everywhere — the Google path lowercases, so the
    local paths must too, or case-variant duplicates split into two accounts."""
    return (email or "").strip().lower()


def normalize_domain(domain: str) -> str:
    """Canonical form for a stored/compared domain: lower-case, no leading '@'."""
    return (domain or "").strip().lower().lstrip("@").rstrip(".")


def assert_domain_allowed(email: str, db: Session) -> None:
    """Gate self-service account creation on the allowed-domain list.

    An empty list means unrestricted — see AllowedEmailDomain. Called only from
    paths that *create* an account; authenticating an existing user is never
    gated, or removing a domain would lock out everyone who signed up under it.
    """
    allowed = db.query(AllowedEmailDomain).all()
    if not allowed:
        return

    normalized = normalize_email(email)
    if "@" not in normalized:
        raise HTTPException(status_code=400, detail="Invalid email address.")
    domain = normalized.rsplit("@", 1)[-1]
    for entry in allowed:
        if domain == entry.domain:
            return
        # Suffix match must include the dot: 'dau.ac.in' must not admit
        # 'dau.ac.in.attacker.com', and must not admit 'notdau.ac.in'.
        if entry.include_subdomains and domain.endswith("." + entry.domain):
            return

    raise HTTPException(
        status_code=403,
        detail="Sign-ups are restricted to approved email domains. "
               "Contact your administrator if you believe this is an error.",
    )


# ─── Auth ───────────────────────────────────────────────────

@router.post("/auth/signup", response_model=UserOut)
def signup(req: SignupRequest, db: Session = Depends(get_db)):
    """Unified signup: creates user + personal org + membership."""
    email = normalize_email(req.email)
    assert_domain_allowed(email, db)
    existing = db.query(User).filter(User.email == email).first()
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")

    # Create user
    user = User(
        email=email,
        password_hash=hash_password(req.password),
        full_name=req.full_name,
        platform_role="user",
    )
    db.add(user)
    db.flush()

    # Auto-create personal organization
    org = Organization(name=f"{req.full_name}'s Personal Org")
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


@router.post("/auth/google", response_model=GoogleTokenOut)
def google_auth(req: GoogleAuthRequest, db: Session = Depends(get_db)):
    """Authenticate or register via Google ID token.

    - Returning Google user → login (JWT)
    - Existing email/password user → link Google account, login
    - New user → create account + personal org, login
    """
    # 1. Verify the Google ID token
    idinfo = verify_google_token(req.id_token)
    google_sub = idinfo["sub"]
    email = idinfo.get("email", "").strip().lower()
    email_verified = idinfo.get("email_verified", False)

    if not email or not email_verified:
        raise HTTPException(
            status_code=400,
            detail="Google account email is not verified",
        )

    full_name = idinfo.get("name", "")
    if not full_name:
        given = idinfo.get("given_name", "")
        family = idinfo.get("family_name", "")
        full_name = f"{given} {family}".strip() or email

    # 2. Look up by google_id (fast path for returning Google users)
    created = False
    user = db.query(User).filter(User.google_id == google_sub).first()

    if not user:
        # 3. Look up by email (account linking)
        user = db.query(User).filter(User.email == email).first()
        if user:
            # Link Google to existing account
            user.google_id = google_sub
            user.auth_provider = "both" if user.password_hash else "google"
            db.commit()
        else:
            # 4. Create new Google-only user.
            # Gate here and nowhere else in this handler: the returning-user
            # fast path and the account-linking branch above authenticate
            # people who already exist, and must keep working even if their
            # domain is later removed from the list.
            assert_domain_allowed(email, db)
            created = True
            try:
                user = User(
                    email=email,
                    password_hash=None,
                    full_name=full_name,
                    platform_role="user",
                    google_id=google_sub,
                    auth_provider="google",
                )
                db.add(user)
                db.flush()

                # Auto-create personal organization
                org = Organization(name=f"{full_name}'s Personal Org")
                db.add(org)
                db.flush()

                membership = OrganizationMembership(
                    org_id=org.id,
                    user_id=user.id,
                    role="owner",
                )
                db.add(membership)
                db.commit()
                db.refresh(user)
            except IntegrityError:
                # A concurrent first sign-in for the same account won the
                # unique-constraint race — use the winner's row.
                db.rollback()
                created = False
                user = db.query(User).filter(User.google_id == google_sub).first() \
                    or db.query(User).filter(User.email == email).first()
                if not user:
                    raise

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is disabled")

    # 5. Issue JWT
    token = create_access_token(user.id, user.platform_role)
    return GoogleTokenOut(
        access_token=token,
        platform_role=user.platform_role,
        must_change_password=bool(user.must_change_password),
        is_new_user=created,
    )


@router.post("/auth/login", response_model=TokenOut)
def login(req: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == normalize_email(req.email)).first()
    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not user.password_hash:
        raise HTTPException(
            status_code=400,
            detail="This account uses Google sign-in. Please log in with Google.",
        )
    if not verify_password(req.password, user.password_hash):
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
    ctx=Depends(get_human_context),
    db: Session = Depends(get_db),
):
    """Regenerate the user's personal API key. Accepts JWT or personal API key."""
    from rate_limit import check_key_creation_rate

    user, _api_key = ctx

    check_key_creation_rate(user.id)

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
                "derived_owner_id": get_org_owner(db, o.id),
                "created_at": o.created_at,
            }
            for o in orgs
        ],
    }


# ─── Allowed Signup Domains ────────────────────────────────

@router.get("/auth/signup-policy")
def signup_policy(db: Session = Depends(get_db)):
    """Public: what the signup form needs to show a useful hint.

    Unauthenticated by necessity — it is read before anyone has an account.
    This does publish the institution's domain list; on a campus deployment
    that is not a secret, and the alternative is users hitting an unexplained
    403 after filling in the form.
    """
    entries = db.query(AllowedEmailDomain).order_by(AllowedEmailDomain.domain).all()
    return {
        "restricted": bool(entries),
        "domains": [e.domain for e in entries],
    }


@router.get("/admin/allowed-domains")
def list_allowed_domains(
    admin=Depends(require_role("superadmin")),
    db: Session = Depends(get_db),
):
    """Superadmin: list domains permitted to self-register."""
    entries = db.query(AllowedEmailDomain).order_by(AllowedEmailDomain.domain).all()
    return {
        "restricted": bool(entries),
        "data": [
            {
                "id": e.id,
                "domain": e.domain,
                "include_subdomains": bool(e.include_subdomains),
                "note": e.note,
                "created_by_id": e.created_by_id,
                "created_at": e.created_at,
            }
            for e in entries
        ],
    }


@router.post("/admin/allowed-domains", status_code=201)
def add_allowed_domain(
    req: AllowedDomainCreate,
    admin=Depends(require_role("superadmin")),
    db: Session = Depends(get_db),
):
    """Superadmin: permit a domain to self-register.

    Adding the first entry switches enforcement on for the whole platform —
    until then signup is unrestricted.
    """
    domain = normalize_domain(req.domain)
    if not domain or "@" in domain or "/" in domain or any(c.isspace() for c in domain) or "." not in domain:
        raise HTTPException(
            status_code=400,
            detail="Enter a bare domain such as 'dau.ac.in' — no '@', no path, no spaces.",
        )

    if db.query(AllowedEmailDomain).filter(AllowedEmailDomain.domain == domain).first():
        raise HTTPException(status_code=409, detail=f"Domain '{domain}' is already allowed")

    entry = AllowedEmailDomain(
        domain=domain,
        include_subdomains=bool(req.include_subdomains),
        note=req.note,
        created_by_id=admin.id,
    )
    db.add(entry)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"Domain '{domain}' is already allowed")
    except Exception:
        db.rollback()
        raise
    db.refresh(entry)
    return {
        "id": entry.id,
        "domain": entry.domain,
        "include_subdomains": bool(entry.include_subdomains),
        "note": entry.note,
    }


@router.delete("/admin/allowed-domains/{domain_id}")
def remove_allowed_domain(
    domain_id: str,
    admin=Depends(require_role("superadmin")),
    db: Session = Depends(get_db),
):
    """Superadmin: stop permitting a domain to self-register.

    Existing accounts are unaffected — this governs new signups only. Removing
    the last entry returns the platform to unrestricted signup, which is why
    the response says so explicitly.
    """
    entry = db.query(AllowedEmailDomain).filter(AllowedEmailDomain.id == domain_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Domain not found")

    db.delete(entry)
    db.commit()

    remaining = db.query(AllowedEmailDomain).count()
    return {
        "deleted": domain_id,
        "restricted": bool(remaining),
        "warning": None if remaining else
        "No domains remain — signup is now open to any email address.",
    }


# ─── Password Reset ────────────────────────────────────────

RESET_TOKEN_EXPIRY_SECONDS = 3600  # 1 hour


@router.post("/auth/forgot-password")
def forgot_password(req: ForgotPasswordRequest, db: Session = Depends(get_db)):
    """Generate reset token and send email. Always returns 200 to prevent email enumeration."""
    user = db.query(User).filter(User.email == normalize_email(req.email)).first()
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
