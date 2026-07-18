"""
Rate-limiting helpers.
Stub — will be replaced with a real implementation (e.g. Redis-backed).
"""
from fastapi import HTTPException


def check_key_creation_rate(user_id: str, db=None):
    """Rate-limit API key creation per user. Currently a no-op stub."""
    pass
