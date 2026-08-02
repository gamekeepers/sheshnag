from datetime import datetime, timezone, timedelta
from typing import Tuple

from jose import jwt, JWTError
import bcrypt as _bcrypt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from database import get_db
import secrets
import hashlib
import os

import logging

from google.oauth2 import id_token as google_id_token

_DEFAULT_SECRET_KEY = "batch-ai-platform-secret-change-in-production"
_SECRET_KEY_ENV = os.getenv("SECRET_KEY")

if not _SECRET_KEY_ENV:
    raise RuntimeError(
        "SECRET_KEY is not set. Generate one with: openssl rand -hex 32\n"
        "Hint: export SECRET_KEY=$(openssl rand -hex 32) before starting uvicorn."
    )

SECRET_KEY = _SECRET_KEY_ENV

if SECRET_KEY == _DEFAULT_SECRET_KEY:
    raise RuntimeError(
        "SECRET_KEY is still the default value. This is insecure for production.\n"
        "Generate one with: openssl rand -hex 32"
    )

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24

security = HTTPBearer()


# ─── Password Helpers ───────────────────────────────────────

def hash_password(password: str) -> str:
    p = password.encode("utf-8")
    if len(p) > 72:
        raise HTTPException(
            status_code=400,
            detail="Password must be 72 bytes or fewer characters",
        )
    return _bcrypt.hashpw(
        p,
        _bcrypt.gensalt(),
    ).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    p = plain.encode("utf-8")
    if len(p) > 72:
        return False
    return _bcrypt.checkpw(
        p,
        hashed.encode("utf-8"),
    )


# ─── JWT ────────────────────────────────────────────────────

def create_access_token(user_id: str, platform_role: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": user_id, "platform_role": platform_role, "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


# ─── API Key Helpers ────────────────────────────────────────

def generate_api_key() -> str:
    """Generate a raw API key string."""
    return f"gk-{secrets.token_hex(24)}"


def hash_api_key(raw_key: str) -> str:
    """Return SHA-256 hex digest of the raw API key."""
    return hashlib.sha256(raw_key.encode()).hexdigest()


def get_api_key_prefix(raw_key: str) -> str:
    """Return the first 8 characters of the key for UI display."""
    return raw_key[:8]


# ─── Shared helpers (used by multiple deps) ────────────────

def _resolve_jwt(token: str, db: Session):
    """Decode a JWT token and return the User, or raise 401."""
    from models import User

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    user = db.query(User).filter(
        User.id == user_id,
        User.is_active,
    ).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


def _resolve_personal_api_key(token: str, db: Session) -> Tuple:
    """Resolve a personal API key and return (api_key_record, user), or raise 401."""
    from models import User, ApiKey

    token_hash = hash_api_key(token)
    api_key_entry = db.query(ApiKey).filter(
        ApiKey.key_hash == token_hash,
        ApiKey.key_type == "personal",
        ApiKey.status == "active",
    ).first()
    if not api_key_entry:
        raise HTTPException(status_code=401, detail="Invalid API key")

    if api_key_entry.expires_at is not None and api_key_entry.expires_at < int(datetime.now(timezone.utc).timestamp()):
        raise HTTPException(status_code=401, detail="API key has expired")

    user = db.query(User).filter(
        User.id == api_key_entry.created_by_user_id,
        User.is_active,
    ).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    api_key_entry.last_used_at = int(datetime.now(timezone.utc).timestamp())
    return (api_key_entry, user)


# ─── 1. get_current_user — JWT only ────────────────────────

def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
):
    """Authenticate via JWT bearer token only. Rejects API keys."""
    token = credentials.credentials

    if token.startswith("gk-"):
        raise HTTPException(
            status_code=401,
            detail="API key not allowed on this endpoint — use a JWT bearer token",
        )

    return _resolve_jwt(token, db)


# ─── 2. get_worker_context — Worker API key only ───────────

def get_worker_context(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> Tuple:
    """Authenticate via worker API key. Returns (api_key_record, organization)."""
    from models import ApiKey, Organization

    token = credentials.credentials

    if not token.startswith("gk-"):
        raise HTTPException(
            status_code=401,
            detail="Worker operations require a worker API key",
        )

    token_hash = hash_api_key(token)
    api_key_entry = db.query(ApiKey).filter(
        ApiKey.key_hash == token_hash,
        ApiKey.key_type == "worker",
        ApiKey.status == "active",
    ).first()
    if not api_key_entry:
        raise HTTPException(status_code=401, detail="Invalid worker API key")

    if api_key_entry.expires_at is not None and api_key_entry.expires_at < int(datetime.now(timezone.utc).timestamp()):
        raise HTTPException(status_code=401, detail="API key has expired")

    org = db.query(Organization).filter(
        Organization.id == api_key_entry.org_id,
    ).first()
    if not org:
        raise HTTPException(status_code=401, detail="Organization not found for this worker key")

    api_key_entry.last_used_at = int(datetime.now(timezone.utc).timestamp())
    return (api_key_entry, org)


# ─── 2b. get_file_read_context — human OR worker ───────────

def get_file_read_context(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
):
    """Authenticate a file-content read by EITHER a human (JWT / personal
    key) OR an org worker key — the daemon downloads job input with its
    worker key. Returns ('user', User) or ('worker', Organization); the
    endpoint applies the matching authorization.
    """
    from models import ApiKey, Organization

    token = credentials.credentials

    if not token.startswith("gk-"):
        return ("user", _resolve_jwt(token, db))

    token_hash = hash_api_key(token)
    entry = db.query(ApiKey).filter(
        ApiKey.key_hash == token_hash,
        ApiKey.status == "active",
    ).first()
    if not entry:
        raise HTTPException(status_code=401, detail="Invalid API key")
    if entry.expires_at is not None and entry.expires_at < int(datetime.now(timezone.utc).timestamp()):
        raise HTTPException(status_code=401, detail="API key has expired")
    entry.last_used_at = int(datetime.now(timezone.utc).timestamp())

    if entry.key_type == "worker":
        org = db.query(Organization).filter(Organization.id == entry.org_id).first()
        if not org:
            raise HTTPException(status_code=401, detail="Organization not found for this worker key")
        return ("worker", org)

    # personal key → resolve as the owning user
    _entry, user = _resolve_personal_api_key(token, db)
    return ("user", user)


# ─── 3. get_personal_context — Personal API key only ──────

def get_personal_context(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> Tuple:
    """Authenticate via personal API key. Returns (api_key_record, user)."""

    token = credentials.credentials

    if not token.startswith("gk-"):
        raise HTTPException(
            status_code=401,
            detail="This endpoint requires a personal API key",
        )

    return _resolve_personal_api_key(token, db)


# ─── 4. get_human_context — JWT or personal API key ────────

def get_human_context(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
):
    """Authenticate via JWT or personal API key. Rejects worker keys.

    Returns (user, api_key_or_None) — api_key is set only when auth'd via
    personal key, allowing attribution tracking.
    """
    token = credentials.credentials

    # Personal API key path
    if token.startswith("gk-"):
        api_key_entry, user = _resolve_personal_api_key(token, db)
        return (user, api_key_entry)

    # JWT path
    user = _resolve_jwt(token, db)
    return (user, None)


# ─── Role checker (built on get_current_user — JWT only) ──

def require_role(*roles):
    """Dependency factory: require user to have one of the given platform_roles.

    Built on get_current_user (JWT only). Maps legacy 'admin' to 'superadmin'.
    """
    def checker(user=Depends(get_current_user)):
        effective_role = user.platform_role
        if effective_role == "admin":
            effective_role = "superadmin"
        if effective_role not in roles:
            raise HTTPException(
                status_code=403,
                detail=f"Access denied. Required role: {', '.join(roles)}",
            )
        return user
    return checker


# ─── Google OAuth ──────────────────────────────────────────

def verify_google_token(token: str) -> dict:
    """Verify a Google ID token and return the decoded payload.

    Returns dict with keys: sub, email, email_verified, name,
    given_name, family_name, picture, etc.

    Raises HTTPException 401 on invalid/expired tokens.
    """
    # Read at call time, not import time: an import-time constant freezes
    # whatever the env held when the module loaded, making config fixes
    # invisible until a full process restart (and the error message a lie).
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    if not client_id:
        raise HTTPException(
            status_code=500,
            detail="Google OAuth is not configured (GOOGLE_CLIENT_ID not set)",
        )
    try:
        idinfo = google_id_token.verify_oauth2_token(
            token, google_requests.Request(), client_id
        )
        if idinfo["iss"] not in ("accounts.google.com", "https://accounts.google.com"):
            raise ValueError("Invalid issuer")
        return idinfo
    except ValueError as e:
        raise HTTPException(status_code=401, detail=f"Invalid Google token: {e}")
