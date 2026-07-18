import logging
from typing import Set

from daemon.executors.ollama import OllamaExecutor
from daemon.client import BackendClient

logger = logging.getLogger(__name__)

class ModelManager:
    """
    Manages model availability and downloads via Ollama.
    """
    
    def __init__(self, executor: OllamaExecutor, client: BackendClient, worker_id: str):
        self._executor = executor
        self._client = client
        self._worker_id = worker_id
        self._available_models: Set[str] = set()
    
    async def refresh_models(self):
        """Refresh the list of locally available models."""
        models = await self._executor.list_models()
        self._available_models = set(models)
    
    async def ensure_model(self, model_name: str) -> bool:
        """
        Ensure a model is available locally. Download if needed.
        Reports download progress to the platform.
        """
        if model_name in self._available_models:
            return True
        
        # Check with Ollama directly
        await self.refresh_models()
        if model_name in self._available_models:
            return True
            
        logger.info(f"Model '{model_name}' not found locally — downloading via Ollama...")
        
        async def progress_callback(progress):
            await self._client.report_model_download(
                worker_id=self._worker_id,
                model_name=model_name,
                status=progress.get("status", "downloading"),
                completed=progress.get("completed", 0),
                total=progress.get("total", 0),
            )
        
        success = await self._executor.pull_model(model_name, progress_callback)
        if not success:
            return False

        # Never trust the pull's return value alone — re-verify against
        # the runtime's own inventory (catches partial downloads and any
        # error mode the stream didn't surface).
        await self.refresh_models()
        return model_name in self._available_models
