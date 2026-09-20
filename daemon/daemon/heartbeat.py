import asyncio
import time
import logging
from datetime import datetime, timezone
from typing import Awaitable, Callable, List, Optional, Dict

from daemon.hardware import available_ram_gb, get_gpu_utilization

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
        get_loaded_model_digests: Optional[Callable[[], Awaitable[Dict]]] = None,
        get_inventory: Optional[Callable[[], Awaitable[List[Dict]]]] = None,
        declared_vram_gb: float = 0.0,
        runtimes: Optional[List[str]] = None,
    ):
        self._client = client
        self._worker_id = worker_id
        self._interval = interval
        self._get_loaded_models = get_loaded_models
        self._get_loaded_model_digests = get_loaded_model_digests
        self._get_inventory = get_inventory
        self._declared_vram_gb = declared_vram_gb
        # Which engines this worker drives. Only used to keep the
        # zero-VRAM warning honest: llama.cpp is dispatchable without a GPU.
        self._runtimes = list(runtimes or [])
        self._warned_zero_vram = False
        self._running = False
        self._task: Optional[asyncio.Task] = None

        self._status = "idle"
        self._current_job_id: Optional[str] = None
        self._progress: Optional[Dict] = None
        self._start_time = time.time()

    def _maybe_warn_zero_vram(self) -> None:
        """Say once that this worker advertises no VRAM, and what follows.

        The #53 failure mode is registering fine, heartbeating fine, showing
        "online" and never getting a batch, with nothing logged.

        What follows depends on the runtime. llama.cpp fits against VRAM plus
        free system RAM, so a host with no GPU is dispatchable for anything
        that fits in RAM; every other runtime fits on VRAM alone and this
        worker is unreachable to them.
        """
        if self._warned_zero_vram:
            return
        self._warned_zero_vram = True
        if "llamacpp" in self._runtimes:
            logger.warning(
                "Advertising 0 GB VRAM. llama.cpp can still be assigned models "
                "that fit in free system RAM; any other runtime on this worker "
                "will never be assigned a batch. Set DAEMON_VRAM_GB if GPU "
                "probing is not supported here."
            )
        else:
            logger.warning(
                "Advertising 0 GB VRAM — the scheduler will never assign this "
                "worker a batch. Set DAEMON_VRAM_GB if GPU probing is not "
                "supported on this host."
            )

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

    def update_worker_id(self, worker_id: str):
        """Adopt the backend-assigned worker id after registration.

        The constructor value is only the local placeholder — the
        control plane assigns the real id at register time, after this
        manager is built.
        """
        self._worker_id = worker_id

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

    async def _fetch_loaded_model_digests(self) -> Dict:
        """Best-effort name → digest map for loaded models."""
        if self._get_loaded_model_digests is None:
            return {}
        try:
            return dict(await self._get_loaded_model_digests())
        except Exception as e:
            logger.debug(f"Could not fetch model digests for heartbeat: {e}")
            return {}

    async def _fetch_inventory(self) -> List[Dict]:
        """Best-effort full on-disk inventory (artifact file hashes)."""
        if self._get_inventory is None:
            return []
        try:
            return list(await self._get_inventory())
        except Exception as e:
            logger.debug(f"Could not fetch inventory for heartbeat: {e}")
            return []

    async def _build_payload(self):
        # Off the event loop: nvidia-smi is a subprocess with a hard
        # timeout — a wedged driver may cost this worker thread 5s, but
        # the poll/execute loop keeps running.
        gpu_stats = await asyncio.to_thread(get_gpu_utilization)
        memory_total = gpu_stats.get("memory_total_gb", 0.0)
        memory_used = gpu_stats.get("memory_used_gb", 0.0)
        # Also off the loop: a subprocess on macOS. None where unreadable —
        # sent as-is, because "unknown" and "none left" are different states.
        ram_available = await asyncio.to_thread(available_ram_gb)

        # An operator-declared capacity (DAEMON_VRAM_GB) overrides probing, so
        # a provider can lend less than the card holds. It is also the only
        # way a host we cannot probe reports anything but 0 — and 0 makes the
        # scheduler's VRAM guard reject this worker for every batch, silently
        # and permanently (scheduler.find_best_batch).
        if self._declared_vram_gb:
            memory_total = self._declared_vram_gb
        if not memory_total:
            self._maybe_warn_zero_vram()

        # Available memory is only meaningful when "used" is a real machine-
        # wide reading. Unified memory (Apple Silicon) has none → None, so
        # the dashboard shows "—" instead of a saturated worker as fully free.
        # With a declared total smaller than the card, clamp: a 24 GB card at
        # 20 GB used lent as 12 GB is not "0 free" for the lease.
        if memory_used is None:
            memory_available = None
        else:
            memory_used = min(memory_used, memory_total)
            memory_available = round(max(memory_total - memory_used, 0.0), 2)

        loaded_models = await self._fetch_loaded_models()
        loaded_set = set(loaded_models)
        inventory = [
            # The worker stamps each item with its own runtime's loaded
            # state (a model live on one runtime is not loaded on
            # another); keep it when present, fall back to the union for
            # items that carry no flag.
            dict(item, loaded=item.get("loaded") if item.get("loaded") is not None
                 else item.get("local_name") in loaded_set)
            for item in await self._fetch_inventory()
        ]
        return {
            "worker_id": self._worker_id,
            # Activity (idle | busy | downloading_model) — distinct from the
            # backend-managed liveness status (online/offline), spec §8.1.
            "activity": self._status,
            "current_job_id": self._current_job_id,
            "progress": self._progress,
            "gpu_utilization": gpu_stats.get("utilization", 0.0),
            "gpu_memory_used_gb": memory_used if memory_used is not None else 0.0,
            "vram_total_gb": memory_total,
            "vram_available_gb": memory_available,
            "ram_available_gb": ram_available,
            "loaded_models": loaded_models,
            "loaded_model_digests": await self._fetch_loaded_model_digests(),
            # Full on-disk inventory with artifact file hashes — the
            # registry's identity join. Resent whole every beat (10-100
            # entries) so drift (a manual `ollama pull`) surfaces on the
            # next beat. Kept alongside loaded_models for rolling upgrade.
            "inventory": inventory,
            "uptime_seconds": int(time.time() - self._start_time),
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
