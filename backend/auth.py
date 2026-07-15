from datetime import datetime, timezone, timedelta
from jose import jwt, JWTError
from passlib.context import CryptContext
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from database import get_db
import secrets
import hashlib

SECRET_KEY = "batch-ai-platform-secret-change-in-production"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer()


# ─── Password Helpers ───────────────────────────────────────

def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


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


# ─── Auth: get_current_user ─────────────────────────────────

def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
):
    """Authenticate via JWT or API key (gk-...).

    Worker keys: authenticate the key's org, mark user as machine identity.
    Personal keys: authenticate the key's owning user directly.
    """
    from models import User, ApiKey
    token = credentials.credentials

    # API key auth path
    if token.startswith("gk-"):
        token_hash = hash_api_key(token)
        api_key_entry = db.query(ApiKey).filter(
            ApiKey.key_hash == token_hash,
            ApiKey.status == "active",
        ).first()
        if not api_key_entry:
            raise HTTPException(status_code=401, detail="Invalid API key")

        # Check expiration before resolving user identity
        if api_key_entry.expires_at is not None and api_key_entry.expires_at < int(datetime.now(timezone.utc).timestamp()):
            raise HTTPException(status_code=401, detail="API key has expired")

        if api_key_entry.key_type == "worker":
            # Worker key — resolve the org owner as a proxy identity
            user = db.query(User).filter(
                User.id == api_key_entry.created_by_user_id,
                User.is_active,
            ).first()
            if not user:
                raise HTTPException(status_code=401, detail="User not found")

            # Mark as machine identity so downstream routes can enforce scope
            user._is_machine_identity = True
            user._api_key_type = "worker"
            user.active_org_id = api_key_entry.org_id

        elif api_key_entry.key_type == "personal":
            # Personal key — authenticate as the key's creator
            user = db.query(User).filter(
                User.id == api_key_entry.created_by_user_id,
                User.is_active,
            ).first()
            if not user:
                raise HTTPException(status_code=401, detail="User not found")

            user._api_key_type = "personal"
        else:
            raise HTTPException(status_code=401, detail="Unknown key type")

        # Update last_used_at only after full auth succeeds.
        # No explicit db.commit() — SQLAlchemy will flush on the session boundary,
        # avoiding a hot-path disk write on every request.
        api_key_entry.last_used_at = int(datetime.now(timezone.utc).timestamp())
        return user

    # JWT auth path
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


def require_role(*roles):
    """Dependency factory: require user to have one of the given platform_roles.
    Maps legacy 'admin' to 'superadmin' for compatibility.
    Rejects machine identities (worker keys)."""
    def checker(user=Depends(get_current_user)):
        # Worker keys cannot impersonate platform roles
        if getattr(user, "_is_machine_identity", False):
            raise HTTPException(
                status_code=403,
                detail="This endpoint requires user authentication (JWT or personal API key)",
            )
        effective_role = user.platform_role
        # Legacy compat: treat "admin" as "superadmin"
        if effective_role == "admin":
            effective_role = "superadmin"
        if effective_role not in roles:
            raise HTTPException(
                status_code=403,
                detail=f"Access denied. Required role: {', '.join(roles)}",
            )
        return user
    return checker


def require_worker_key(user=Depends(get_current_user)):
    """Require an API key of type 'worker'. Rejects JWT and personal keys."""
    if getattr(user, "_api_key_type", None) != "worker":
        raise HTTPException(
            status_code=403,
            detail="Worker operations require a worker API key",
        )
    return user


def require_human_user(user=Depends(get_current_user)):
    """Require a real user (JWT or personal key). Rejects worker keys."""
    if getattr(user, "_is_machine_identity", False):
        raise HTTPException(
            status_code=403,
            detail="This endpoint requires user authentication (JWT or personal API key)",
        )
    return user
