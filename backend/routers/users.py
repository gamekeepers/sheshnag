from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import ApiKey, unix_now
from schemas import ApiKeyCreate, ApiKeyUpdate, ApiKeyOut
from auth import (
    get_human_context,
    generate_api_key,
    hash_api_key,
    get_api_key_prefix,
)
from rate_limit import check_key_creation_rate

router = APIRouter()


# ─── POST /users/me/api-keys — Create personal key ─────────


@router.post("/users/me/api-keys")
def create_personal_api_key(
    body: ApiKeyCreate,
    ctx=Depends(get_human_context),
    db: Session = Depends(get_db),
):
    """Create a new personal API key.

    The raw key is returned **once** — store it securely. Subsequent requests
    only show the prefix for identification.
    """
    user, _api_key = ctx
    check_key_creation_rate(user.id)

    # Validate expires_at is in the future and not too far out
    if body.expires_at is not None:
        now = int(datetime.now(timezone.utc).timestamp())
        if body.expires_at <= now:
            raise HTTPException(status_code=400, detail="expires_at must be in the future")
        if body.expires_at > now + (365 * 24 * 3600):
            raise HTTPException(
                status_code=400,
                detail="expires_at cannot be more than 1 year in the future",
            )

    raw_key = generate_api_key()

    api_key_entry = ApiKey(
        org_id=None,
        key_type="personal",
        created_by_user_id=user.id,
        name=body.name,
        key_prefix=get_api_key_prefix(raw_key),
        key_hash=hash_api_key(raw_key),
        status="active",
        expires_at=body.expires_at,
    )

    db.add(api_key_entry)
    db.commit()
    db.refresh(api_key_entry)

    return {
        "id": api_key_entry.id,
        "api_key": raw_key,
        "name": api_key_entry.name,
        "key_prefix": api_key_entry.key_prefix,
        "status": api_key_entry.status,
        "key_type": api_key_entry.key_type,
        "expires_at": api_key_entry.expires_at,
        "created_at": api_key_entry.created_at,
    }


# ─── GET /users/me/api-keys — List personal keys ───────────


@router.get("/users/me/api-keys")
def list_personal_api_keys(
    ctx=Depends(get_human_context),
    db: Session = Depends(get_db),
):
    """List all personal API keys for the current user.

    Full key values are **never** returned — only the prefix for identification.
    """
    user, _api_key = ctx

    keys = (
        db.query(ApiKey)
        .filter(
            ApiKey.created_by_user_id == user.id,
            ApiKey.key_type == "personal",
        )
        .order_by(ApiKey.created_at.desc())
        .all()
    )

    return {
        "object": "list",
        "data": [
            {
                "id": k.id,
                "name": k.name,
                "key_prefix": k.key_prefix,
                "status": k.status,
                "key_type": k.key_type,
                "expires_at": k.expires_at,
                "last_used_at": k.last_used_at,
                "created_at": k.created_at,
            }
            for k in keys
        ],
    }


# ─── PUT /users/me/api-keys/{key_id} — Update personal key ──


@router.put("/users/me/api-keys/{key_id}")
def update_personal_api_key(
    key_id: str,
    body: ApiKeyUpdate,
    ctx=Depends(get_human_context),
    db: Session = Depends(get_db),
):
    """Update a personal API key's name, expiry, or status.

    The actual key value cannot be changed — revoke and create a new one instead.
    """
    user, _api_key = ctx

    api_key_entry = db.query(ApiKey).filter(
        ApiKey.id == key_id,
        ApiKey.created_by_user_id == user.id,
        ApiKey.key_type == "personal",
    ).first()

    if not api_key_entry:
        raise HTTPException(status_code=404, detail="API key not found")

    if body.name is not None:
        api_key_entry.name = body.name

    if body.expires_at is not None:
        now = int(datetime.now(timezone.utc).timestamp())
        if body.expires_at <= now:
            raise HTTPException(status_code=400, detail="expires_at must be in the future")
        if body.expires_at > now + (365 * 24 * 3600):
            raise HTTPException(
                status_code=400,
                detail="expires_at cannot be more than 1 year in the future",
            )
        api_key_entry.expires_at = body.expires_at

    if body.status is not None:
        api_key_entry.status = body.status
        if body.status == "revoked":
            api_key_entry.revoked_at = unix_now()

    db.commit()
    db.refresh(api_key_entry)

    return {
        "id": api_key_entry.id,
        "name": api_key_entry.name,
        "key_prefix": api_key_entry.key_prefix,
        "status": api_key_entry.status,
        "key_type": api_key_entry.key_type,
        "expires_at": api_key_entry.expires_at,
        "last_used_at": api_key_entry.last_used_at,
        "created_at": api_key_entry.created_at,
        "revoked_at": api_key_entry.revoked_at,
    }


# ─── DELETE /users/me/api-keys/{key_id} — Revoke personal key


@router.delete("/users/me/api-keys/{key_id}")
def revoke_personal_api_key(
    key_id: str,
    ctx=Depends(get_human_context),
    db: Session = Depends(get_db),
):
    """Revoke a personal API key.

    This is a soft-delete — the record is kept for audit purposes with
    status set to 'revoked'.
    """
    user, _api_key = ctx

    api_key_entry = db.query(ApiKey).filter(
        ApiKey.id == key_id,
        ApiKey.created_by_user_id == user.id,
        ApiKey.key_type == "personal",
    ).first()

    if not api_key_entry:
        raise HTTPException(status_code=404, detail="API key not found")

    if api_key_entry.status == "revoked":
        raise HTTPException(
            status_code=400,
            detail="API key is already revoked",
        )

    api_key_entry.status = "revoked"
    api_key_entry.revoked_at = unix_now()

    db.commit()

    return {
        "id": api_key_entry.id,
        "status": "revoked",
        "detail": "API key revoked successfully",
    }
