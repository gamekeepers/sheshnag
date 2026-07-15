import os
import json
import logging
from pathlib import Path
from typing import Optional

from daemon.hardware import detect_hardware
from daemon.models import WorkerInfo

logger = logging.getLogger(__name__)

class RegistrationManager:
    def __init__(self, credentials_path: str):
        self._credentials_path = Path(credentials_path)
        self._credentials_path.parent.mkdir(parents=True, exist_ok=True)

    async def register(self, client, config) -> str:
        """
        Register worker with the control plane, returning the API key.
        Will re-register if we already have an API key to update hardware/models.
        """
        logger.info("Detecting hardware for registration...")
        hardware = detect_hardware()
        
        worker_info = WorkerInfo(
            worker_id=config.worker_id,
            provider_id=config.provider_id,
            hardware=hardware,
            models=config.models,
            runtime=config.runtime,
            status="online"
        )
        
        logger.info(f"Registering worker {config.worker_id} (Provider: {config.provider_id})")
        result = await client.register_worker(worker_info)
        
        api_key = result.get("api_key")
        if not api_key:
            raise ValueError("Registration failed: No API key returned from platform.")
            
        self._save_credentials(api_key, config.worker_id)
        os.environ["DAEMON_API_KEY"] = api_key
        return api_key

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

    def _save_credentials(self, api_key: str, worker_id: str):
        """Persist API key to disk."""
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
