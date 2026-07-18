import json
import logging
from typing import Optional, List, Callable, Awaitable

import httpx

from daemon.executors.base import BaseExecutor
from daemon.models import CompletionResult, PromptRequest

logger = logging.getLogger(__name__)

class OllamaExecutor(BaseExecutor):
    """
    Executor for Ollama inference runtime.
    
    Ollama API:
        POST /api/chat     - chat completions
        POST /api/generate - text generation
        GET  /api/tags     - list available models
        POST /api/pull     - download a model
        GET  /api/ps       - list running models
    """
    
    def __init__(self, base_url: str = "http://localhost:11434", timeout: float = 300.0):
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None
        
    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(self._timeout),
            )
        return self._client
    
    async def execute(self, prompt: PromptRequest) -> CompletionResult:
        """
        Execute a prompt via Ollama's /api/chat endpoint.
        Translates from OpenAI format to Ollama's format, then back.
        """
        client = self._get_client()
        ollama_body = self._translate_request(prompt.body)
        
        try:
            response = await client.post("/api/chat", json=ollama_body)
            response.raise_for_status()
            
            openai_response = self._translate_response(response.json())
            return CompletionResult(
                custom_id=prompt.custom_id, 
                response=openai_response
            )
        except Exception as e:
            logger.error(f"Ollama execution failed for {prompt.custom_id}: {e}")
            return CompletionResult(
                custom_id=prompt.custom_id,
                error=str(e)
            )
    
    async def health_check(self) -> bool:
        """Check Ollama is running via GET /api/tags."""
        client = self._get_client()
        try:
            response = await client.get("/api/tags", timeout=5.0)
            return response.status_code == 200
        except Exception:
            return False
            
    async def pull_model(self, model_name: str, progress_callback: Optional[Callable[[dict], Awaitable[None]]] = None) -> bool:
        """
        Pull/download a model via Ollama's POST /api/pull.
        Streams progress and reports via callback.
        """
        client = self._get_client()
        try:
            async with client.stream("POST", "/api/pull", json={"name": model_name}) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    try:
                        progress = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    # Ollama reports failures as {"error": ...} events in a
                    # 200 stream (bad model name, disk full, registry errors)
                    # — raise_for_status never sees them.
                    if "error" in progress:
                        logger.error(
                            f"Ollama pull failed for {model_name}: {progress['error']}"
                        )
                        return False
                    if progress_callback:
                        await progress_callback(progress)
            return True
        except Exception as e:
            logger.error(f"Failed to pull model {model_name}: {e}")
            return False
            
    async def list_models(self) -> List[str]:
        """List locally available model names via GET /api/tags."""
        return [m["name"] for m in await self.list_models_detailed()]

    async def list_models_detailed(self) -> List[dict]:
        """List local models with their digests via GET /api/tags.

        Returns [{"name": ..., "digest": ...}]. The digest is the
        artifact's reproducibility anchor (see the model catalogue).
        """
        client = self._get_client()
        try:
            response = await client.get("/api/tags", timeout=10.0)
            response.raise_for_status()
            data = response.json()
            return [
                {"name": m["name"], "digest": m.get("digest")}
                for m in data.get("models", [])
                if m.get("name")
            ]
        except Exception as e:
            logger.error(f"Failed to list models: {e}")
            return []
            
    def _translate_request(self, openai_body: dict) -> dict:
        """OpenAI chat format -> Ollama chat format."""
        return {
            "model": openai_body.get("model", ""),
            "messages": openai_body.get("messages", []),
            "stream": False,
            "options": {
                "temperature": openai_body.get("temperature", 0.7),
                "num_predict": openai_body.get("max_tokens", 512),
            }
        }
        
    def _translate_response(self, ollama_response: dict) -> dict:
        """Ollama response -> OpenAI-compatible response format."""
        return {
            "choices": [{
                "index": 0,
                "message": ollama_response.get("message", {}),
                "finish_reason": "stop" if ollama_response.get("done") else "length",
            }],
            "model": ollama_response.get("model", ""),
            "usage": {
                "prompt_tokens": ollama_response.get("prompt_eval_count", 0),
                "completion_tokens": ollama_response.get("eval_count", 0),
                "total_tokens": (
                    ollama_response.get("prompt_eval_count", 0) +
                    ollama_response.get("eval_count", 0)
                ),
            }
        }
