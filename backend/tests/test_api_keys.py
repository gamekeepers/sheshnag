"""Tests for personal API key CRUD endpoints and related security guards.

Covers:
- Key creation returns raw key once; DB stores only hash
- List never returns full key value
- Update rejects invalid status (Pydantic Literal)
- Revoke sets revoked_at timestamp
- Delete is soft-delete (status -> 'revoked', record kept)
- Double revoke returns 400
- Machine identity cannot create/list keys
- Expired key is rejected at auth time
- Rate limiting on key creation (max 10/hour)
- Expiration validation (must be future, max 1 year)
"""

import hashlib
import time
from datetime import datetime, timezone

import pytest


# ─── Test: create_key_returns_raw_key_once ──────────────────────

def test_create_key_returns_raw_key_once(auth_client, db_session):
    """POST /users/me/api-keys returns the full key; DB stores only hash."""
    from models import ApiKey

    resp = auth_client.post("/v1/users/me/api-keys", json={
        "name": "Test Key",
    })
    assert resp.status_code == 200, f"Unexpected: {resp.text}"

    data = resp.json()
    assert "api_key" in data
    assert data["api_key"].startswith("gk-")
    assert data["name"] == "Test Key"
    assert data["key_type"] == "personal"
    assert data["status"] == "active"

    # DB stores only the hash, not the raw key
    raw_key = data["api_key"]
    expected_hash = hashlib.sha256(raw_key.encode()).hexdigest()

    db_key = db_session.query(ApiKey).filter(
        ApiKey.id == data["id"],
    ).first()
    assert db_key is not None
    assert db_key.key_hash == expected_hash


# ─── Test: list_keys_never_returns_full_key ─────────────────────

def test_list_keys_never_returns_full_key(auth_client, db_session):
    """GET /users/me/api-keys returns only key_prefix, never full key."""
    # Create a key first
    create_resp = auth_client.post("/v1/users/me/api-keys", json={"name": "List Test"})
    assert create_resp.status_code == 200
    raw_key = create_resp.json()["api_key"]

    # Now list keys
    list_resp = auth_client.get("/v1/users/me/api-keys")
    assert list_resp.status_code == 200
    data = list_resp.json()
    assert data["object"] == "list"

    for key_data in data["data"]:
        # Full key value should never appear in list response
        assert raw_key != key_data.get("key_prefix")
        assert raw_key not in str(key_data)
        assert "api_key" not in key_data


# ─── Test: update_key_rejects_invalid_status ──────────────────

def test_update_key_rejects_invalid_status(auth_client):
    """PUT /users/me/api-keys/{id} with invalid status returns 422."""
    # Create a key first
    create_resp = auth_client.post("/v1/users/me/api-keys", json={"name": "Status Test"})
    assert create_resp.status_code == 200
    key_id = create_resp.json()["id"]

    # Try to update with invalid status — Pydantic Literal should reject it
    resp = auth_client.put(f"/v1/users/me/api-keys/{key_id}", json={
        "status": "invalid_status",
    })
    assert resp.status_code == 422


# ─── Test: update_key_revokes_with_timestamp ──────────────────

def test_update_key_revokes_with_timestamp(auth_client, db_session):
    """PUT with {status: 'revoked'} sets revoked_at."""
    from models import ApiKey

    create_resp = auth_client.post("/v1/users/me/api-keys", json={"name": "Revoke Test"})
    assert create_resp.status_code == 200
    key_id = create_resp.json()["id"]

    # Revoke via update
    before_revoke = int(datetime.now(timezone.utc).timestamp())
    resp = auth_client.put(f"/v1/users/me/api-keys/{key_id}", json={
        "status": "revoked",
    })
    after_revoke = int(datetime.now(timezone.utc).timestamp())
    assert resp.status_code == 200

    update_data = resp.json()
    assert update_data["status"] == "revoked"
    assert update_data.get("revoked_at") is not None

    # Verify DB has revoked_at set
    db_key = db_session.query(ApiKey).filter(ApiKey.id == key_id).first()
    assert db_key is not None
    assert db_key.status == "revoked"
    assert db_key.revoked_at is not None
    assert before_revoke <= db_key.revoked_at <= after_revoke


# ─── Test: delete_key_is_soft_delete ──────────────────────────

def test_delete_key_is_soft_delete(auth_client, db_session):
    """DELETE /users/me/api-keys/{id} keeps the record with status='revoked'."""
    from models import ApiKey

    create_resp = auth_client.post("/v1/users/me/api-keys", json={"name": "Delete Test"})
    assert create_resp.status_code == 200
    key_id = create_resp.json()["id"]

    # Delete (revoke) the key
    resp = auth_client.delete(f"/v1/users/me/api-keys/{key_id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "revoked"

    # Record still exists in DB
    db_key = db_session.query(ApiKey).filter(ApiKey.id == key_id).first()
    assert db_key is not None
    assert db_key.status == "revoked"
    assert db_key.revoked_at is not None


# ─── Test: double_revoke_returns_400 ──────────────────────────

def test_double_revoke_returns_400(auth_client):
    """Revoke a key twice — second call returns 400."""
    create_resp = auth_client.post("/v1/users/me/api-keys", json={"name": "Double Revoke"})
    assert create_resp.status_code == 200
    key_id = create_resp.json()["id"]

    # First revoke succeeds
    resp1 = auth_client.delete(f"/v1/users/me/api-keys/{key_id}")
    assert resp1.status_code == 200

    # Second revoke fails
    resp2 = auth_client.delete(f"/v1/users/me/api-keys/{key_id}")
    assert resp2.status_code == 400


# ─── Test: machine_identity_cannot_create_keys ────────────────

def test_machine_identity_cannot_create_keys(auth_client, db_session):
    """Worker key auth context cannot POST /users/me/api-keys (403)."""
    from models import ApiKey, User
    from auth import generate_api_key, hash_api_key, get_api_key_prefix

    # Create a worker API key for the test user's org
    user = db_session.query(User).filter(User.email == "test@example.com").first()
    assert user is not None

    # Get the user's org
    from models import OrganizationMembership
    membership = db_session.query(OrganizationMembership).filter(
        OrganizationMembership.user_id == user.id,
    ).first()
    assert membership is not None

    raw_key = generate_api_key()
    worker_key = ApiKey(
        org_id=membership.org_id,
        key_type="worker",
        created_by_user_id=user.id,
        name="Test Worker Key",
        key_prefix=get_api_key_prefix(raw_key),
        key_hash=hash_api_key(raw_key),
        status="active",
    )
    db_session.add(worker_key)
    db_session.commit()

    # Use the worker key to try creating a personal API key
    headers = {"Authorization": f"Bearer {raw_key}"}
    resp = auth_client.post(
        "/v1/users/me/api-keys",
        json={"name": "Should Fail"},
        headers=headers,
    )
    # Worker keys are rejected at the dep level (401) rather than after
    # authentication (403), since get_human_context only accepts JWT or
    # personal keys — worker keys never resolve as a valid identity.
    assert resp.status_code == 401


# ─── Test: machine_identity_cannot_list_keys ──────────────────

def test_machine_identity_cannot_list_keys(auth_client, db_session):
    """Worker key auth context cannot GET /users/me/api-keys (403)."""
    from models import ApiKey, User
    from auth import generate_api_key, hash_api_key, get_api_key_prefix

    user = db_session.query(User).filter(User.email == "test@example.com").first()
    assert user is not None

    from models import OrganizationMembership
    membership = db_session.query(OrganizationMembership).filter(
        OrganizationMembership.user_id == user.id,
    ).first()
    assert membership is not None

    raw_key = generate_api_key()
    worker_key = ApiKey(
        org_id=membership.org_id,
        key_type="worker",
        created_by_user_id=user.id,
        name="Test Worker Key 2",
        key_prefix=get_api_key_prefix(raw_key),
        key_hash=hash_api_key(raw_key),
        status="active",
    )
    db_session.add(worker_key)
    db_session.commit()

    headers = {"Authorization": f"Bearer {raw_key}"}
    resp = auth_client.get("/v1/users/me/api-keys", headers=headers)
    # Same reasoning as test above — worker key rejected at dep level.
    assert resp.status_code == 401


# ─── Test: expired_key_is_rejected_at_auth ────────────────────

def test_expired_key_is_rejected_at_auth(auth_client, db_session):
    """API key with past expires_at is rejected at auth time (401)."""
    from models import ApiKey, User
    from auth import generate_api_key, hash_api_key, get_api_key_prefix

    user = db_session.query(User).filter(User.email == "test@example.com").first()

    raw_key = generate_api_key()
    expired_key = ApiKey(
        org_id=None,
        key_type="personal",
        created_by_user_id=user.id,
        name="Expired Key",
        key_prefix=get_api_key_prefix(raw_key),
        key_hash=hash_api_key(raw_key),
        status="active",
        expires_at=int(datetime.now(timezone.utc).timestamp()) - 3600,  # 1 hour ago
    )
    db_session.add(expired_key)
    db_session.commit()

    # Try to authenticate with the expired key
    headers = {"Authorization": f"Bearer {raw_key}"}
    resp = auth_client.get("/v1/users/me/api-keys", headers=headers)
    assert resp.status_code == 401


# ─── Test: create_key_rate_limit ──────────────────────────────

def test_create_key_rate_limit(auth_client, db_session):
    """Create more than 10 keys in an hour — 11th returns 429."""
    from models import User
    from rate_limit import _key_creation_timestamps, MAX_KEYS_PER_HOUR

    user = db_session.query(User).filter(User.email == "test@example.com").first()
    user_id = user.id

    # Clear any existing timestamps for this user
    _key_creation_timestamps.pop(user_id, None)

    # Create 10 keys — should all succeed
    for i in range(MAX_KEYS_PER_HOUR):
        resp = auth_client.post("/v1/users/me/api-keys", json={
            "name": f"Rate Limit Key {i}",
        })
        assert resp.status_code == 200, f"Key {i} creation failed: {resp.text}"

    # 11th key should be rate-limited
    resp = auth_client.post("/v1/users/me/api-keys", json={
        "name": "Rate Limited Key",
    })
    assert resp.status_code == 429

    # Cleanup
    _key_creation_timestamps.pop(user_id, None)


# ─── Test: expires_at_must_be_future ──────────────────────────

def test_expires_at_must_be_future(auth_client):
    """POST with past expires_at returns 400."""
    past_timestamp = int(datetime.now(timezone.utc).timestamp()) - 3600

    resp = auth_client.post("/v1/users/me/api-keys", json={
        "name": "Past Expiry Key",
        "expires_at": past_timestamp,
    })
    assert resp.status_code == 400


def test_expires_at_max_one_year(auth_client):
    """POST with expires_at more than 1 year in future returns 400."""
    future_timestamp = int(datetime.now(timezone.utc).timestamp()) + (366 * 24 * 3600)

    resp = auth_client.post("/v1/users/me/api-keys", json={
        "name": "Too Far Future Key",
        "expires_at": future_timestamp,
    })
    assert resp.status_code == 400
