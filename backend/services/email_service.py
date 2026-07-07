"""
Mailgun email service.
Credentials are loaded from environment variables (set in .env file).
"""
import os
import logging
import requests

logger = logging.getLogger(__name__)

MAILGUN_API_KEY = os.getenv("MAILGUN_API_KEY")
MAILGUN_DOMAIN = os.getenv("MAILGUN_DOMAIN")
MAILGUN_FROM = os.getenv("MAILGUN_FROM", "Moonknight support <noreply@moonknight.gamekeepers.in>")


def send_email(to_email: str, subject: str, text: str) -> bool:
    """Send an email via Mailgun. Returns True on success."""
    if not MAILGUN_API_KEY or not MAILGUN_DOMAIN:
        logger.warning("Mailgun not configured — skipping email to %s", to_email)
        return False

    try:
        response = requests.post(
            f"https://api.mailgun.net/v3/{MAILGUN_DOMAIN}/messages",
            auth=("api", MAILGUN_API_KEY),
            data={
                "from": MAILGUN_FROM,
                "to": to_email,
                "subject": subject,
                "text": text,
            },
        )
        if response.status_code == 200:
            logger.info("Email sent to %s", to_email)
            return True
        else:
            logger.warning("Mailgun error %s: %s", response.status_code, response.text)
            return False
    except Exception as e:
        logger.error("Failed to send email: %s", e)
        return False


def send_password_reset_email(to_email: str, reset_token: str, frontend_url: str = None) -> bool:
    """Send password reset email with token."""
    if not frontend_url:
        frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000")

    reset_link = f"{frontend_url}/reset-password?token={reset_token}"

    subject = "Password Reset — Moonknight Platform"
    text = (
        f"You requested a password reset for your Moonknight account.\n\n"
        f"Click the link below to reset your password:\n"
        f"{reset_link}\n\n"
        f"This link expires in 1 hour.\n"
        f"If you didn't request this, ignore this email.\n"
    )
    return send_email(to_email, subject, text)
