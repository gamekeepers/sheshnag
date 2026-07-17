import asyncio
import time
import logging
from datetime import datetime, timezone
from typing import Awaitable, Callable, List, Optional, Dict

from daemon.hardware import get_gpu_utilization

logger = logging.getLogger(__name__)

class HeartbeatManager:
    """
    Sends periodic heartbeats to the platform.

    The payload carries the dynamic properties the scheduler matches on
    (spec §8.1): VRAM total/available and currently loaded models, plus
    activity status and job progress.
    """

    def __init__(
        self,
        client,
        worker_id: str,
        interval: int = 30,
        get_loaded_models: Optional[Callable[[], Awaitable[List[str]]]] = None,
    ):
        self._client = client
        self._worker_id = worker_id
        self._interval = interval
        self._get_loaded_models = get_loaded_models
        self._running = False
        self._task: Optional[asyncio.Task] = None

        self._status = "idle"
        self._current_job_id: Optional[str] = None
        self._progress: Optional[Dict] = None
        self._start_time = time.time()

    async def start(self):
        """Start the heartbeat background loop."""
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info(f"Heartbeat manager started (interval={self._interval}s)")

    async def stop(self):
        """Stop the heartbeat loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Heartbeat manager stopped")

    def update_status(self, status: str, job_id: Optional[str] = None, progress: Optional[Dict] = None):
        """Called by Worker to update heartbeat payload."""
        self._status = status
        self._current_job_id = job_id
        self._progress = progress

    async def _loop(self):
        while self._running:
            try:
                payload = await self._build_payload()
                await self._client.send_heartbeat(payload)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Heartbeat failed: {e}")

            await asyncio.sleep(self._interval)

    async def _fetch_loaded_models(self) -> List[str]:
        """Best-effort query of the runtime's loaded models."""
        if self._get_loaded_models is None:
            return []
        try:
            return list(await self._get_loaded_models())
        except Exception as e:
            logger.debug(f"Could not fetch loaded models for heartbeat: {e}")
            return []

    async def _build_payload(self):
        gpu_stats = get_gpu_utilization()
        memory_total = gpu_stats.get("memory_total_gb", 0.0)
        memory_used = gpu_stats.get("memory_used_gb", 0.0)
        return {
            "worker_id": self._worker_id,
            # Activity (idle | busy | downloading_model) — distinct from the
            # backend-managed liveness status (online/offline), spec §8.1.
            "activity": self._status,
            "current_job_id": self._current_job_id,
            "progress": self._progress,
            "gpu_utilization": gpu_stats.get("utilization", 0.0),
            "gpu_memory_used_gb": memory_used,
            "vram_total_gb": memory_total,
            "vram_available_gb": round(max(memory_total - memory_used, 0.0), 2),
            "loaded_models": await self._fetch_loaded_models(),
            "uptime_seconds": int(time.time() - self._start_time),
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
