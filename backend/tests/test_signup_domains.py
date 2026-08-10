"""
Allowed-domain gating for self-service signup.

The behaviour that matters most is not "blocks the wrong domain" — it is the
two things easy to get wrong in the opposite direction:

  * an empty list must allow everything, or a fresh install locks out the
    superadmin needed to add the first domain;
  * gating must apply to account *creation* only, never authentication, or
    removing a domain silently locks out everyone who signed up under it.
"""

import pytest

from models import AllowedEmailDomain, User
from routers.auth import assert_domain_allowed, normalize_domain
from fastapi import HTTPException


@pytest.fixture
def domains(db_session):
    """Manage rows directly; each test declares the list it wants."""
    db_session.query(AllowedEmailDomain).delete()
    db_session.commit()

    def _add(domain, include_subdomains=False):
        db_session.add(AllowedEmailDomain(
            domain=domain, include_subdomains=include_subdomains
        ))
        db_session.commit()

    yield _add
    db_session.query(AllowedEmailDomain).delete()
    db_session.commit()


def _allowed(email, db):
    try:
        assert_domain_allowed(email, db)
        return True
    except HTTPException:
        return False


# ─── Fail-open on an empty list ─────────────────────────────

def test_empty_list_allows_anything(db_session, domains):
    """Enforcement is opt-in. Without this, a fresh install cannot be set up."""
    assert _allowed("anyone@gmail.com", db_session)
    assert _allowed("someone@example.org", db_session)


# ─── Matching ───────────────────────────────────────────────

def test_exact_domain_allowed(db_session, domains):
    domains("dau.ac.in")
    assert _allowed("student@dau.ac.in", db_session)


def test_other_domain_rejected(db_session, domains):
    domains("dau.ac.in")
    assert not _allowed("someone@gmail.com", db_session)


def test_subdomain_rejected_when_flag_off(db_session, domains):
    domains("dau.ac.in", include_subdomains=False)
    assert not _allowed("student@cse.dau.ac.in", db_session)


def test_subdomain_allowed_when_flag_on(db_session, domains):
    domains("dau.ac.in", include_subdomains=True)
    assert _allowed("student@cse.dau.ac.in", db_session)


def test_lookalike_suffix_rejected(db_session, domains):
    """The suffix match must include the separating dot.

    'notdau.ac.in' ends with 'dau.ac.in' as a plain string — matching on bare
    endswith() would admit an attacker-registered lookalike.
    """
    domains("dau.ac.in", include_subdomains=True)
    assert not _allowed("attacker@notdau.ac.in", db_session)


def test_domain_suffix_extension_rejected(db_session, domains):
    """'dau.ac.in.attacker.com' must not pass — it is a different domain."""
    domains("dau.ac.in", include_subdomains=True)
    assert not _allowed("attacker@dau.ac.in.attacker.com", db_session)


def test_case_and_whitespace_normalised(db_session, domains):
    domains("dau.ac.in")
    assert _allowed("  Student@DAU.AC.IN  ", db_session)


def test_multiple_domains_any_match_allows(db_session, domains):
    domains("dau.ac.in")
    domains("daiict.ac.in")
    assert _allowed("x@daiict.ac.in", db_session)
    assert not _allowed("x@elsewhere.edu", db_session)


@pytest.mark.parametrize("raw,expected", [
    ("DAU.ac.in", "dau.ac.in"),
    ("@dau.ac.in", "dau.ac.in"),
    ("  dau.ac.in  ", "dau.ac.in"),
    ("dau.ac.in.", "dau.ac.in"),
])
def test_domain_normalisation(raw, expected):
    assert normalize_domain(raw) == expected


# ─── Through the signup endpoint ────────────────────────────

def test_signup_blocked_for_disallowed_domain(auth_client, db_session, domains):
    domains("dau.ac.in")
    res = auth_client.post("/v1/auth/signup", json={
        "email": "outsider@gmail.com", "password": "testpassword123",
        "full_name": "Outsider",
    })
    assert res.status_code == 403
    assert "restricted" in res.json()["detail"].lower()
    assert db_session.query(User).filter(User.email == "outsider@gmail.com").first() is None


def test_signup_allowed_for_permitted_domain(auth_client, domains):
    domains("dau.ac.in")
    res = auth_client.post("/v1/auth/signup", json={
        "email": "newstudent@dau.ac.in", "password": "testpassword123",
        "full_name": "New Student",
    })
    assert res.status_code < 400, res.text


# ─── Authentication must never be gated ─────────────────────

def test_existing_user_can_still_log_in_after_domain_removed(auth_client, db_session, domains):
    """The regression that would hurt most: tighten the list, lock out real users.

    Sign up under an allowed domain, then remove it. Login must still work —
    the gate belongs on creation only.
    """
    domains("dau.ac.in")
    signup = auth_client.post("/v1/auth/signup", json={
        "email": "staying@dau.ac.in", "password": "testpassword123",
        "full_name": "Staying User",
    })
    assert signup.status_code < 400, signup.text

    db_session.query(AllowedEmailDomain).delete()
    db_session.add(AllowedEmailDomain(domain="somewhere-else.edu"))
    db_session.commit()

    login = auth_client.post("/v1/auth/login", json={
        "email": "staying@dau.ac.in", "password": "testpassword123",
    })
    assert login.status_code == 200, login.text


# ─── Admin CRUD ─────────────────────────────────────────────

def test_signup_policy_is_public_and_reports_state(auth_client, domains):
    assert auth_client.get("/v1/auth/signup-policy").json()["restricted"] is False
    domains("dau.ac.in")
    body = auth_client.get("/v1/auth/signup-policy").json()
    assert body["restricted"] is True
    assert body["domains"] == ["dau.ac.in"]


def test_non_superadmin_cannot_list_domains(auth_client):
    assert auth_client.get("/v1/admin/allowed-domains").status_code == 403


def test_non_superadmin_cannot_add_domain(auth_client):
    res = auth_client.post("/v1/admin/allowed-domains", json={"domain": "evil.com"})
    assert res.status_code == 403


def test_superadmin_can_add_and_remove(superadmin_client, domains):
    res = superadmin_client.post("/v1/admin/allowed-domains", json={
        "domain": "  DAU.ac.in ", "include_subdomains": True, "note": "Students",
    })
    assert res.status_code == 201, res.text
    assert res.json()["domain"] == "dau.ac.in"      # normalised on write
    domain_id = res.json()["id"]

    listing = superadmin_client.get("/v1/admin/allowed-domains").json()
    assert listing["restricted"] is True
    assert listing["data"][0]["include_subdomains"] is True

    gone = superadmin_client.delete(f"/v1/admin/allowed-domains/{domain_id}")
    assert gone.status_code == 200
    # Removing the last domain reopens signup — say so rather than leave it implicit.
    assert gone.json()["restricted"] is False
    assert "open to any email" in gone.json()["warning"]


def test_duplicate_domain_rejected(superadmin_client, domains):
    superadmin_client.post("/v1/admin/allowed-domains", json={"domain": "dau.ac.in"})
    dup = superadmin_client.post("/v1/admin/allowed-domains", json={"domain": "DAU.AC.IN"})
    assert dup.status_code == 409


@pytest.mark.parametrize("bad", ["", "not a domain", "user@dau.ac.in", "dau.ac.in/path", "localhost"])
def test_malformed_domain_rejected(superadmin_client, domains, bad):
    assert superadmin_client.post("/v1/admin/allowed-domains", json={"domain": bad}).status_code == 400


def test_deleting_unknown_domain_404s(superadmin_client):
    assert superadmin_client.delete("/v1/admin/allowed-domains/domain-nope").status_code == 404


# ─── Google sign-in path ────────────────────────────────────
#
# The most important coverage in this file. Google account creation runs
# through a completely separate branch from password signup, so gating only
# the password path would leave self-registration wide open — and nothing
# checks the OIDC `hd` claim, so any personal Gmail reaches this code.

def _idinfo(sub, email, verified=True, name="Google User"):
    return {"sub": sub, "email": email, "email_verified": verified, "name": name}


@pytest.fixture
def google_token(monkeypatch):
    from routers import auth as auth_router

    def _install(**kw):
        monkeypatch.setattr(auth_router, "verify_google_token", lambda tok: _idinfo(**kw))
    return _install


def test_google_signup_blocked_for_disallowed_domain(auth_client, db_session, domains, google_token):
    domains("dau.ac.in")
    google_token(sub="g-blocked-1", email="outsider@gmail.com")

    res = auth_client.post("/v1/auth/google", json={"id_token": "x"})
    assert res.status_code == 403
    assert db_session.query(User).filter(User.email == "outsider@gmail.com").first() is None


def test_google_signup_allowed_for_permitted_domain(auth_client, domains, google_token):
    domains("dau.ac.in")
    google_token(sub="g-allowed-1", email="gstudent@dau.ac.in")

    res = auth_client.post("/v1/auth/google", json={"id_token": "x"})
    assert res.status_code == 200, res.text
    assert res.json()["is_new_user"] is True


def test_existing_google_user_still_signs_in_after_domain_removed(
    auth_client, db_session, domains, google_token
):
    """Authentication is never gated — only creation.

    Without this distinction, tightening the domain list would lock out every
    Google user who had already signed up.
    """
    domains("dau.ac.in")
    google_token(sub="g-returning-1", email="returning@dau.ac.in")
    first = auth_client.post("/v1/auth/google", json={"id_token": "x"})
    assert first.status_code == 200, first.text

    db_session.query(AllowedEmailDomain).delete()
    db_session.add(AllowedEmailDomain(domain="somewhere-else.edu"))
    db_session.commit()

    again = auth_client.post("/v1/auth/google", json={"id_token": "x"})
    assert again.status_code == 200, again.text
    assert again.json()["is_new_user"] is False


def test_google_linking_to_existing_account_is_not_gated(
    auth_client, db_session, domains, google_token
):
    """A password user whose domain later falls off the list can still link Google."""
    domains("dau.ac.in")
    auth_client.post("/v1/auth/signup", json={
        "email": "linkme@dau.ac.in", "password": "testpassword123", "full_name": "Link Me",
    })

    db_session.query(AllowedEmailDomain).delete()
    db_session.add(AllowedEmailDomain(domain="somewhere-else.edu"))
    db_session.commit()

    google_token(sub="g-link-1", email="linkme@dau.ac.in")
    res = auth_client.post("/v1/auth/google", json={"id_token": "x"})
    assert res.status_code == 200, res.text
    assert res.json()["is_new_user"] is False
