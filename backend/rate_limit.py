"""Lightweight in-memory rate limiting for API key creation.

No external dependencies — tracks per-user request timestamps and enforces
a max count within a sliding window.

Caveats (acceptable for V1, not for horizontal scale):
  - **Per-process, resets on restart**: state lives in this process's
    memory. With N uvicorn workers the effective limit is
    N × MAX_KEYS_PER_HOUR, and it resets on restart. A shared store
    (e.g. Redis) is needed for a truly global limit.
  - Memory is bounded by a periodic sweep that evicts users whose window
    has fully expired (see `_sweep`).
  - A lock guards the state so concurrent requests (FastAPI runs sync
    endpoints in a threadpool) don't corrupt the per-user lists.
"""

import threading
import time

from fastapi import HTTPException

# { user_id: [timestamp, ...] }
_key_creation_timestamps = {}
_lock = threading.Lock()
_last_sweep = 0.0

MAX_KEYS_PER_HOUR = 10
WINDOW_SECONDS = 3600


def _sweep(now: float) -> None:
    """Evict users whose timestamps have all aged out — bounds memory to
    users active within roughly the last window. Runs at most once per
    window. Caller must hold `_lock`."""
    global _last_sweep
    if now - _last_sweep < WINDOW_SECONDS:
        return
    _last_sweep = now
    for uid in list(_key_creation_timestamps):
        kept = [t for t in _key_creation_timestamps[uid] if now - t < WINDOW_SECONDS]
        if kept:
            _key_creation_timestamps[uid] = kept
        else:
            del _key_creation_timestamps[uid]


def check_key_creation_rate(user_id: str):
    """Raise 429 if this user has exceeded the key creation rate limit."""
    now = time.time()

    with _lock:
        _sweep(now)

        timestamps = [
            t for t in _key_creation_timestamps.get(user_id, [])
            if now - t < WINDOW_SECONDS
        ]

        if len(timestamps) >= MAX_KEYS_PER_HOUR:
            _key_creation_timestamps[user_id] = timestamps  # keep sliding window
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded: max {MAX_KEYS_PER_HOUR} keys per hour",
            )

        timestamps.append(now)
        _key_creation_timestamps[user_id] = timestamps
