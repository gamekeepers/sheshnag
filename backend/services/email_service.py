"""
Mailgun email service — placeholder per AC's instructions.
Domain setup in progress. Will be integrated once ready.
"""
import os
import requests


MAILGUN_API_KEY = os.getenv(
    "MAILGUN_API_KEY",
    "ca34a192155658d07ae5083d6e3c12f3-3330bd33-3f3a6b49",
)
MAILGUN_DOMAIN = os.getenv(
    "MAILGUN_DOMAIN",
    "sandbox56d98216f33444579ebc0f56959a01a7.mailgun.org",
)
FROM_EMAIL = f"Moonknight support <postmaster@{MAILGUN_DOMAIN}>"


def send_email(to_name, to_email, subject, text):
    """Send an email via Mailgun."""
    return requests.post(
        f"https://api.mailgun.net/v3/{MAILGUN_DOMAIN}/messages",
        auth=("api", MAILGUN_API_KEY),
        data={
            "from": FROM_EMAIL,
            "to": f"{to_name} <{to_email}>",
            "subject": subject,
            "text": text,
        },
    )
