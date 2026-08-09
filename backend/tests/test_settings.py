"""Settings accessor tests.

A wildcard origin combined with `allow_credentials=True` is rejected outright
by browsers, so every `cors_origins` parse result is asserted together with the
`cors_allow_credentials` it produces rather than separately.
"""

import pytest

from settings import DEV_SECRET_KEY, settings


# ─── CORS parsing ────────────────────────────────────────────

@pytest.mark.parametrize(
    "raw, expected_origins, expected_credentials",
    [
        # Unset falls back to the wildcard default.
        (None, ["*"], False),
        # Explicit wildcard.
        ("*", ["*"], False),
        # Blank and all-empty values mean "unset", not "block everything" —
        # an empty allow list would reject every browser request.
        ("", ["*"], False),
        ("   ", ["*"], False),
        (",", ["*"], False),
        (" , , ", ["*"], False),
        # A single explicit origin allows credentials.
        ("https://app.example.com", ["https://app.example.com"], True),
        # Multiple origins, with surrounding whitespace stripped and empty
        # entries from a trailing comma dropped.
        (
            "https://a.example.com, https://b.example.com,",
            ["https://a.example.com", "https://b.example.com"],
            True,
        ),
        # A wildcard mixed into an explicit list still disables credentials —
        # the browser would reject the response otherwise.
        ("https://a.example.com,*", ["https://a.example.com", "*"], False),
    ],
)
def test_cors_origins_and_credentials(monkeypatch, raw, expected_origins, expected_credentials):
    if raw is None:
        monkeypatch.delenv("CORS_ORIGINS", raising=False)
    else:
        monkeypatch.setenv("CORS_ORIGINS", raw)

    assert settings.cors_origins == expected_origins
    assert settings.cors_allow_credentials is expected_credentials


def test_credentials_never_allowed_alongside_wildcard(monkeypatch):
    """The invariant the derivation exists to protect, stated directly."""
    for raw in ["*", "", "https://a.example.com,*", "*,https://a.example.com"]:
        monkeypatch.setenv("CORS_ORIGINS", raw)
        assert not (
            "*" in settings.cors_origins and settings.cors_allow_credentials
        ), f"wildcard plus credentials for CORS_ORIGINS={raw!r}"


# ─── Lazy read ───────────────────────────────────────────────

def test_values_are_read_on_access_not_frozen_at_import(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "https://first.example.com")
    assert settings.cors_origins == ["https://first.example.com"]

    monkeypatch.setenv("CORS_ORIGINS", "https://second.example.com")
    assert settings.cors_origins == ["https://second.example.com"]


# ─── Other accessors ─────────────────────────────────────────

def test_secret_key_falls_back_to_dev_key_and_flags_itself(monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    assert settings.secret_key == DEV_SECRET_KEY
    assert settings.using_dev_secret_key is True

    # An empty value is as unset as a missing one — `or` rather than a default.
    monkeypatch.setenv("SECRET_KEY", "")
    assert settings.secret_key == DEV_SECRET_KEY
    assert settings.using_dev_secret_key is True

    monkeypatch.setenv("SECRET_KEY", "a-real-key")
    assert settings.secret_key == "a-real-key"
    assert settings.using_dev_secret_key is False


def test_email_configured_requires_both_mailgun_values(monkeypatch):
    monkeypatch.delenv("MAILGUN_API_KEY", raising=False)
    monkeypatch.delenv("MAILGUN_DOMAIN", raising=False)
    assert settings.email_configured is False

    monkeypatch.setenv("MAILGUN_API_KEY", "key")
    assert settings.email_configured is False

    monkeypatch.setenv("MAILGUN_DOMAIN", "mg.example.com")
    assert settings.email_configured is True


def test_url_defaults(monkeypatch):
    monkeypatch.delenv("FRONTEND_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert settings.frontend_url == "http://localhost:3005"
    assert settings.database_url == "sqlite:///./jobs.db"
