"""
Mailgun email service.
Credentials are loaded from environment variables (set in .env file).
"""
import logging
import requests

from settings import settings

logger = logging.getLogger(__name__)


def send_email(to_email: str, subject: str, text: str) -> bool:
    """Send an email via Mailgun. Returns True on success."""
    if not settings.email_configured:
        logger.warning("Mailgun not configured — skipping email to %s", to_email)
        return False

    try:
        response = requests.post(
            f"https://api.mailgun.net/v3/{settings.mailgun_domain}/messages",
            auth=("api", settings.mailgun_api_key),
            data={
                "from": settings.mailgun_from,
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
        frontend_url = settings.frontend_url

    reset_link = f"{frontend_url}/reset-password?token={reset_token}"

    subject = "Password Reset — Sheshnag Platform"
    text = (
        f"You requested a password reset for your Sheshnag account.\n\n"
        f"Click the link below to reset your password:\n"
        f"{reset_link}\n\n"
        f"This link expires in 1 hour.\n"
        f"If you didn't request this, ignore this email.\n"
    )
    return send_email(to_email, subject, text)
