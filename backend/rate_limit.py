"""Lightweight in-memory rate limiting for API key creation.

No external dependencies — tracks per-user request timestamps and enforces
a max count within a sliding window. Resets on process restart.
"""

import time
from fastapi import HTTPException

# { user_id: [timestamp, timestamp, ...] }
_key_creation_timestamps = {}

MAX_KEYS_PER_HOUR = 10
WINDOW_SECONDS = 3600


def check_key_creation_rate(user_id: str):
    """Raise 429 if this user has exceeded the key creation rate limit."""
    now = time.time()

    if user_id not in _key_creation_timestamps:
        _key_creation_timestamps[user_id] = []

    # Prune entries outside the sliding window
    _key_creation_timestamps[user_id] = [
        t for t in _key_creation_timestamps[user_id]
        if now - t < WINDOW_SECONDS
    ]

    if len(_key_creation_timestamps[user_id]) >= MAX_KEYS_PER_HOUR:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: max {MAX_KEYS_PER_HOUR} keys per hour",
        )

    _key_creation_timestamps[user_id].append(now)
