"""When a worker counts as alive.

One definition for everything that asks: the sweeper reclaiming silent
workers, pool capacity, the validator's "is anyone online" check, and the
requeue-affinity hold. Imports only `models`, so any of them can import it
without a cycle.
"""
from models import unix_now

# Daemon heartbeats every 30s — 4 missed beats ⇒ presumed dead.
HEARTBEAT_TIMEOUT_SECONDS = 120


def worker_is_live(worker) -> bool:
    """Online, and heard from within HEARTBEAT_TIMEOUT_SECONDS. The sweeper
    flips a silent worker offline only once a minute, so the heartbeat is
    checked here too rather than trusting `status` alone."""
    return (worker.status == "online"
            and (worker.last_heartbeat or 0) >= unix_now() - HEARTBEAT_TIMEOUT_SECONDS)
