import os
import json
import logging
from pathlib import Path
from typing import Optional

from daemon.hardware import detect_hardware
from daemon.models import WorkerInfo

logger = logging.getLogger(__name__)

class RegistrationManager:
    """
    Handles worker registration with the control plane.

    The org worker API key (spec §8.0/§17) is an *input*: it is created
    in the dashboard and configured on the daemon (config/env/CLI).
    Registration authenticates with it and returns the backend-assigned
    worker_id, which is persisted locally alongside the key so restarts
    reuse the same worker identity.
    """

    def __init__(self, credentials_path: str):
        self._credentials_path = Path(credentials_path)
        self._credentials_path.parent.mkdir(parents=True, exist_ok=True)

    async def register(self, client, config) -> str:
        """
        Register worker with the control plane, returning the assigned
        worker_id. Re-registering (same hostname + org) updates the
        worker's hardware/models on the backend.
        """
        logger.info("Detecting hardware for registration...")
        hardware = detect_hardware()

        worker_info = WorkerInfo(
            worker_id=config.worker_id,
            hardware=hardware,
            models=config.models,
            runtime=config.runtime,
            status="online"
        )

        logger.info(f"Registering worker {config.worker_id}")
        result = await client.register_worker(worker_info)

        assigned_worker_id = result.get("worker_id", config.worker_id)
        self._save_credentials(config.api_key, assigned_worker_id)
        return assigned_worker_id

    def load_saved_credentials(self) -> Optional[str]:
        """Load API key from the credentials file."""
        if not self._credentials_path.exists():
            return None
        try:
            with open(self._credentials_path, "r") as f:
                data = json.load(f)
                return data.get("api_key")
        except Exception as e:
            logger.warning(f"Failed to read credentials file: {e}")
            return None

    def load_saved_worker_id(self) -> Optional[str]:
        """Load the backend-assigned worker id from the credentials file."""
        if not self._credentials_path.exists():
            return None
        try:
            with open(self._credentials_path, "r") as f:
                data = json.load(f)
                return data.get("worker_id")
        except Exception as e:
            logger.warning(f"Failed to read credentials file: {e}")
            return None

    def _save_credentials(self, api_key: str, worker_id: str):
        """Persist API key + assigned worker id to disk."""
        data = {
            "api_key": api_key,
            "worker_id": worker_id
        }
        try:
            with open(self._credentials_path, "w") as f:
                json.dump(data, f)
            # Secure the file (Unix only)
            if os.name == 'posix':
                self._credentials_path.chmod(0o600)
        except Exception as e:
            logger.error(f"Failed to save credentials: {e}")
