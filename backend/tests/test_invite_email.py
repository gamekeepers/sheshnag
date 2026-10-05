"""
InviteRequest.email was a plain str while EmailStr was imported and unused.

A typo'd address was accepted by the API and then failed later at send time.
The schema now rejects it as a validation error, which FastAPI surfaces as 422.
"""

import pytest
from pydantic import ValidationError

from routers.organizations import InviteRequest


def test_invite_request_accepts_a_valid_email():
    req = InviteRequest(email="person@example.com")
    assert req.email == "person@example.com"


def test_invite_request_rejects_a_typo_address():
    """Issue #76: not-an-email must not become a silent send failure."""
    with pytest.raises(ValidationError):
        InviteRequest(email="not-an-email")


def test_invite_request_rejects_empty_email():
    with pytest.raises(ValidationError):
        InviteRequest(email="")
