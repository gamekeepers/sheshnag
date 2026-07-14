from datetime import datetime, timezone, timedelta
from jose import jwt, JWTError
from passlib.context import CryptContext
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from database import get_db
import secrets

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


# ─── Auth: get_current_user ─────────────────────────────────

def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
):
    """Authenticate via JWT or API key (gk-...).
    
    API key path: ApiKey table -> Organization -> owner User.
    Sets user.active_org_id for downstream use.
    """
    from models import User, ApiKey, Organization
    token = credentials.credentials

    # API key auth path
    if token.startswith("gk-"):
        api_key_entry = db.query(ApiKey).filter(ApiKey.key == token).first()
        if not api_key_entry:
            raise HTTPException(status_code=401, detail="Invalid API key")

        org = db.query(Organization).filter(Organization.id == api_key_entry.org_id).first()
        if not org:
            raise HTTPException(status_code=401, detail="Organization not found for this key")

        user = db.query(User).filter(User.id == org.owner_id, User.is_active == True).first()
        if not user:
            raise HTTPException(status_code=401, detail="User not found")

        # Attach active org context
        user.active_org_id = org.id
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
        User.is_active == True,
    ).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


def require_role(*roles):
    """Dependency factory: require user to have one of the given platform_roles.
    Maps legacy 'admin' to 'superadmin' for compatibility."""
    def checker(user=Depends(get_current_user)):
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
