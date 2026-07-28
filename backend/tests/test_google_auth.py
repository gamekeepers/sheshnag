"""Tests for Google OAuth sign-up / login (Issue #30).

Uses a monkeypatch to stub verify_google_token so we never hit
Google's servers — we control the returned payload.
"""

import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from database import Base, get_db
from models import User, Organization, OrganizationMembership


# ─── Helpers ────────────────────────────────────────────────

FAKE_GOOGLE_SUB = "google-uid-123456789"
FAKE_EMAIL = "googleuser@gmail.com"
FAKE_NAME = "Jane Doe"

def _fake_google_payload(
    sub=FAKE_GOOGLE_SUB,
    email=FAKE_EMAIL,
    name=FAKE_NAME,
    email_verified=True,
):
    """Build a minimal Google ID token payload."""
    return {
        "iss": "https://accounts.google.com",
        "sub": sub,
        "email": email,
        "email_verified": email_verified,
        "name": name,
        "given_name": name.split()[0] if name else "",
        "family_name": name.split()[-1] if name and " " in name else "",
        "picture": "https://lh3.googleusercontent.com/a/default",
    }


# ─── Test: New user signup via Google ───────────────────────

def test_google_signup_new_user(auth_client, _engine, db_session):
    """A brand new Google user should be auto-created with personal org."""
    payload = _fake_google_payload(
        sub="new-google-user-sub",
        email="newgoogle@gmail.com",
        name="New Googler",
    )

    with patch("routers.auth.verify_google_token", return_value=payload):
        resp = auth_client.post("/v1/auth/google", json={"id_token": "fake-token"})

    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert data["platform_role"] == "user"

    # Verify user was created in DB
    user = db_session.query(User).filter(User.email == "newgoogle@gmail.com").first()
    assert user is not None
    assert user.google_id == "new-google-user-sub"
    assert user.auth_provider == "google"
    assert user.password_hash is None
    assert user.full_name == "New Googler"

    # Verify personal org was created
    membership = db_session.query(OrganizationMembership).filter(
        OrganizationMembership.user_id == user.id,
        OrganizationMembership.role == "owner",
    ).first()
    assert membership is not None


# ─── Test: Returning Google user login ──────────────────────

def test_google_login_returning_user(auth_client, _engine, db_session):
    """A user who already signed up with Google should just get a JWT."""
    # First, create the user via Google
    payload = _fake_google_payload(
        sub="returning-google-sub",
        email="returning@gmail.com",
        name="Return User",
    )

    with patch("routers.auth.verify_google_token", return_value=payload):
        resp1 = auth_client.post("/v1/auth/google", json={"id_token": "fake-token"})
    assert resp1.status_code == 200

    # Login again — same Google sub
    with patch("routers.auth.verify_google_token", return_value=payload):
        resp2 = auth_client.post("/v1/auth/google", json={"id_token": "fake-token-2"})

    assert resp2.status_code == 200
    assert "access_token" in resp2.json()

    # Should still be only one user with this email
    users = db_session.query(User).filter(User.email == "returning@gmail.com").all()
    assert len(users) == 1


# ─── Test: Account linking — existing password user ─────────

def test_google_links_existing_password_user(auth_client, _engine, db_session):
    """An existing email/password user signing in with Google should be linked."""
    from auth import hash_password

    # Create a password-based user directly
    SM = sessionmaker(bind=_engine, expire_on_commit=False)
    with SM() as s:
        user = User(
            email="linkme@example.com",
            password_hash=hash_password("mypassword"),
            full_name="Link Me",
            platform_role="user",
            auth_provider="local",
        )
        s.add(user)
        s.flush()
        org = Organization(name="Link Me's Org")
        s.add(org)
        s.flush()
        s.add(OrganizationMembership(org_id=org.id, user_id=user.id, role="owner"))
        s.commit()
        user_id = user.id

    payload = _fake_google_payload(
        sub="link-google-sub",
        email="linkme@example.com",
        name="Link Me",
    )

    with patch("routers.auth.verify_google_token", return_value=payload):
        resp = auth_client.post("/v1/auth/google", json={"id_token": "fake-token"})

    assert resp.status_code == 200

    # User should now have google_id and auth_provider="both"
    linked_user = db_session.query(User).filter(User.id == user_id).first()
    assert linked_user.google_id == "link-google-sub"
    assert linked_user.auth_provider == "both"


# ─── Test: Google-only user blocked from password login ─────

def test_google_only_user_blocked_from_password_login(auth_client, _engine, db_session):
    """Google-only users (no password) should get a clear error on POST /auth/login."""
    # Create Google user
    payload = _fake_google_payload(
        sub="nopassword-sub",
        email="nopassword@gmail.com",
        name="No Password",
    )

    with patch("routers.auth.verify_google_token", return_value=payload):
        auth_client.post("/v1/auth/google", json={"id_token": "fake-token"})

    # Try password login
    resp = auth_client.post("/v1/auth/login", json={
        "email": "nopassword@gmail.com",
        "password": "anything",
    })

    assert resp.status_code == 400
    assert "Google sign-in" in resp.json()["detail"]


# ─── Test: Unverified email rejected ───────────────────────

def test_google_unverified_email_rejected(auth_client):
    """Google accounts with unverified email should be rejected."""
    payload = _fake_google_payload(email_verified=False)

    with patch("routers.auth.verify_google_token", return_value=payload):
        resp = auth_client.post("/v1/auth/google", json={"id_token": "fake-token"})

    assert resp.status_code == 400
    assert "not verified" in resp.json()["detail"]


# ─── Test: Invalid token returns 401 ───────────────────────

def test_google_invalid_token(auth_client):
    """An invalid Google token should return 401."""
    from fastapi import HTTPException

    def raise_401(token):
        raise HTTPException(status_code=401, detail="Invalid Google token")

    with patch("routers.auth.verify_google_token", side_effect=raise_401):
        resp = auth_client.post("/v1/auth/google", json={"id_token": "bad-token"})

    assert resp.status_code == 401
