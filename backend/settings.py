"""Every environment variable the backend reads, declared exactly once.

Previously each variable was read wherever it happened to be needed —
`FRONTEND_URL` in two modules, Mailgun credentials in a third, `SECRET_KEY` in
a fourth — so its name and default were restated at each site and a changed
default meant hunting them all down. Add a variable here and read it through
`settings`; do not call `os.getenv` elsewhere in the backend.

Two deliberate design points:

1. **Values are read on access, not frozen at import.** A module-level
   constant captures whatever the environment held when the module first
   loaded, which makes a config fix invisible until a full restart and turns
   any error message quoting it into a lie. It also breaks `monkeypatch.setenv`
   in tests — `tests/test_auth_google.py` depends on the lazy read.

2. **`load_dotenv()` is called here.** It used to live only at the top of
   `main.py`, so anything imported before it (or without it, like a test
   importing `database` directly) silently saw an unpopulated environment.
   Importing settings now guarantees `.env` is loaded, whatever the entrypoint.
"""

import logging
import os

from dotenv import load_dotenv

# Idempotent, and does not override variables already set in the real
# environment — so container/systemd config still wins over a stray .env file.
load_dotenv()

logger = logging.getLogger(__name__)

# Dev fallback. Kept so `cp .env.example .env && uvicorn` still boots, but the
# key is public (it lives in git history), so anything signed with it is
# forgeable — hence the startup warning below.
DEV_SECRET_KEY = "batch-ai-platform-secret-change-in-production"


class Settings:
    """Typed accessors for the backend's environment."""

    # ── Database ──
    @property
    def database_url(self) -> str:
        return os.getenv("DATABASE_URL", "sqlite:///./jobs.db")

    # ── Auth ──
    @property
    def secret_key(self) -> str:
        return os.getenv("SECRET_KEY") or DEV_SECRET_KEY

    @property
    def using_dev_secret_key(self) -> bool:
        return self.secret_key == DEV_SECRET_KEY

    @property
    def google_client_id(self) -> str | None:
        return os.getenv("GOOGLE_CLIENT_ID")

    # ── URLs ──
    @property
    def frontend_url(self) -> str:
        """Base URL for links in password-reset and invite emails."""
        return os.getenv("FRONTEND_URL", "http://localhost:3005")

    # ── Email (Mailgun) ──
    @property
    def mailgun_api_key(self) -> str | None:
        return os.getenv("MAILGUN_API_KEY")

    @property
    def mailgun_domain(self) -> str | None:
        return os.getenv("MAILGUN_DOMAIN")

    @property
    def mailgun_from(self) -> str:
        return os.getenv("MAILGUN_FROM", "Sheshnag support <noreply@sheshnag.io>")

    @property
    def email_configured(self) -> bool:
        """False means send_email() skips rather than fails — email is optional."""
        return bool(self.mailgun_api_key and self.mailgun_domain)

    # ── CORS ──
    @property
    def cors_origins(self) -> list[str]:
        origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()]
        # A blank or all-empty value means "unset", not "block everything".
        return origins or ["*"]

    @property
    def cors_allow_credentials(self) -> bool:
        """Derived, never configured independently: browsers reject a wildcard
        origin when credentials are allowed, so the two must agree."""
        return "*" not in self.cors_origins


settings = Settings()


def warn_on_insecure_defaults() -> None:
    """Log warnings for dev-only defaults left in place. Called at startup."""
    if settings.using_dev_secret_key:
        logger.warning(
            "SECRET_KEY is unset or still the built-in default — JWTs are signed "
            "with a publicly known key and can be forged. Do not run this way in "
            "production. Generate one with: openssl rand -hex 32"
        )
