"""Google OAuth endpoint tests (POST /v1/auth/google).

verify_google_token is monkeypatched at its import site in routers.auth —
these tests exercise the account logic, not Google's signature check.
"""

import pytest

from models import User, Organization, OrganizationMembership
from routers import auth as auth_router


def _idinfo(sub, email, verified=True, name="Google User"):
    return {"sub": sub, "email": email, "email_verified": verified, "name": name}


@pytest.fixture
def google_token(monkeypatch):
    """Install a fake verifier; returns a setter for the idinfo payload."""
    def _install(**kw):
        monkeypatch.setattr(
            auth_router, "verify_google_token", lambda tok: _idinfo(**kw)
        )
    return _install


def test_new_google_user_created_with_personal_org(auth_client, google_token, db_session):
    google_token(sub="g-new-1", email="gnew1@dau.ac.in", name="Fresh Gee")

    res = auth_client.post("/v1/auth/google", json={"id_token": "x"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["access_token"]
    assert body["is_new_user"] is True

    user = db_session.query(User).filter(User.email == "gnew1@dau.ac.in").first()
    assert user is not None
    assert user.google_id == "g-new-1"
    assert user.auth_provider == "google"
    assert user.password_hash is None

    membership = db_session.query(OrganizationMembership).filter(
        OrganizationMembership.user_id == user.id
    ).first()
    assert membership is not None and membership.role == "owner"
    org = db_session.query(Organization).filter(Organization.id == membership.org_id).first()
    assert org is not None


def test_returning_google_user_is_not_new(auth_client, google_token):
    google_token(sub="g-ret-1", email="gret1@dau.ac.in")

    first = auth_client.post("/v1/auth/google", json={"id_token": "x"}).json()
    second = auth_client.post("/v1/auth/google", json={"id_token": "x"}).json()
    assert first["is_new_user"] is True
    assert second["is_new_user"] is False


def test_linking_existing_local_account_case_insensitive(auth_client, google_token, db_session):
    # Local signup with mixed-case email…
    res = auth_client.post("/v1/auth/signup", json={
        "email": "Linker@DAU.ac.in", "password": "secret-pass-123", "full_name": "Link Me",
    })
    assert res.status_code == 200, res.text

    # …then Google sign-in with the lowercase form links, not duplicates.
    google_token(sub="g-link-1", email="linker@dau.ac.in")
    res = auth_client.post("/v1/auth/google", json={"id_token": "x"})
    assert res.status_code == 200, res.text
    assert res.json()["is_new_user"] is False

    accounts = db_session.query(User).filter(User.email == "linker@dau.ac.in").all()
    assert len(accounts) == 1
    assert accounts[0].google_id == "g-link-1"
    assert accounts[0].auth_provider == "both"


def test_unverified_email_rejected(auth_client, google_token):
    google_token(sub="g-unv-1", email="unverified@dau.ac.in", verified=False)
    res = auth_client.post("/v1/auth/google", json={"id_token": "x"})
    assert res.status_code == 400
    assert "not verified" in res.json()["detail"]


def test_google_only_account_blocked_from_password_login(auth_client, google_token):
    google_token(sub="g-only-1", email="gonly1@dau.ac.in")
    assert auth_client.post("/v1/auth/google", json={"id_token": "x"}).status_code == 200

    res = auth_client.post("/v1/auth/login", json={
        "email": "gonly1@dau.ac.in", "password": "whatever",
    })
    assert res.status_code == 400
    assert "Google sign-in" in res.json()["detail"]


def test_invalid_token_rejected(auth_client, monkeypatch):
    """Unmocked verifier with a garbage token → 401 (client id present)."""
    import auth as auth_module
    monkeypatch.setattr(auth_module, "GOOGLE_CLIENT_ID", "dummy-client-id")
    res = auth_client.post("/v1/auth/google", json={"id_token": "not-a-real-token"})
    assert res.status_code == 401
    assert "Invalid Google token" in res.json()["detail"]
