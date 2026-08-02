"""
bcrypt hashes at most 72 bytes. Passwords that get *set* are bounded by the
schema so an over-long one is rejected as a validation error rather than
reaching hash_password and surfacing as a bare 400 — or, worse, being stored
as the hash of a truncated prefix.
"""

import pytest

from auth import hash_password, verify_password
from schemas import BCRYPT_MAX_BYTES, LoginRequest, SignupRequest


def _signup(client, email, password):
    return client.post(
        "/v1/auth/signup",
        json={"email": email, "password": password, "full_name": "Boundary Test"},
    )


# ─── Schema bounds ──────────────────────────────────────────

def test_password_at_the_limit_is_accepted():
    req = SignupRequest(email="a@b.test", password="x" * BCRYPT_MAX_BYTES, full_name="A")
    assert len(req.password) == BCRYPT_MAX_BYTES


def test_password_one_byte_over_is_rejected():
    with pytest.raises(ValueError):
        SignupRequest(email="a@b.test", password="x" * (BCRYPT_MAX_BYTES + 1), full_name="A")


def test_multibyte_password_is_bounded_by_bytes_not_characters():
    """24 three-byte characters is 72 bytes (ok); 25 is 75 bytes (not ok).

    A plain max_length would let the second case through and it would then fail
    deeper in, at hash_password.
    """
    ok = "न" * 24
    too_long = "न" * 25
    assert len(too_long) < BCRYPT_MAX_BYTES  # would pass a character-count check

    SignupRequest(email="a@b.test", password=ok, full_name="A")
    with pytest.raises(ValueError):
        SignupRequest(email="a@b.test", password=too_long, full_name="A")


def test_login_password_is_not_bounded():
    """Checking a password must stay a 401, not become a 422."""
    req = LoginRequest(email="a@b.test", password="x" * 200)
    assert len(req.password) == 200


# ─── Hashing round-trip ─────────────────────────────────────

def test_hash_and_verify_round_trip():
    hashed = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", hashed)
    assert not verify_password("wrong horse battery staple", hashed)


def test_verify_rejects_over_limit_password():
    hashed = hash_password("x" * BCRYPT_MAX_BYTES)
    assert not verify_password("x" * (BCRYPT_MAX_BYTES + 1), hashed)


# ─── Through the API ────────────────────────────────────────

def test_signup_rejects_over_limit_password(auth_client):
    resp = _signup(auth_client, "toolong@dau.ac.in", "x" * (BCRYPT_MAX_BYTES + 1))
    assert resp.status_code == 422


def test_signup_accepts_password_at_limit(auth_client):
    resp = _signup(auth_client, "atlimit@dau.ac.in", "x" * BCRYPT_MAX_BYTES)
    assert resp.status_code < 400, resp.text
