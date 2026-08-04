import json
import logging
from typing import Optional, List, Callable, Awaitable

import httpx
import jsonschema

from daemon.executors.base import BaseExecutor
from daemon.models import CompletionResult, PromptRequest

logger = logging.getLogger(__name__)

def parse_version(version_str: Optional[str]) -> tuple[int, ...]:
    """Semantic version parser that handles pre-releases and v prefix."""
    if not version_str:
        return (0, 0, 0)
    version_str = version_str.lower().strip()
    if version_str.startswith("v"):
        version_str = version_str[1:]
    version_str = version_str.split("-")[0]
    version_str = version_str.split("+")[0]
    
    parts = []
    for part in version_str.split("."):
        numeric_chars = []
        for char in part:
            if char.isdigit():
                numeric_chars.append(char)
            else:
                break
        if numeric_chars:
            parts.append(int("".join(numeric_chars)))
        else:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)

class OllamaExecutor(BaseExecutor):
    """
    Executor for Ollama inference runtime.
    
    Ollama API:
        POST /api/chat     - chat completions
        POST /api/generate - text generation
        GET  /api/tags     - list available models
        POST /api/pull     - download a model
        GET  /api/ps       - list running models
        GET  /api/version  - get version info
    """
    
    def __init__(self, base_url: str = "http://localhost:11434", timeout: float = 300.0):
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None
        self.version: Optional[str] = None
        
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
        # Lazy check version if not set
        if self.version is None:
            await self.health_check()
            
        response_format = prompt.body.get("response_format")
        
        # Version Gating: Refuse JSON mode if Ollama version < 0.5.0 (or undetermined)
        if response_format is not None:
            if self.version is None:
                logger.error("Ollama version undetermined. Server might be unreachable.")
                return CompletionResult(
                    custom_id=prompt.custom_id,
                    error="OLLAMA_UNREACHABLE: could not determine Ollama version — server may be down"
                )
            v_tuple = parse_version(self.version)
            if v_tuple < (0, 5, 0):
                logger.error(
                    f"Ollama version {self.version} < 0.5.0 does not support structured outputs."
                )
                return CompletionResult(
                    custom_id=prompt.custom_id,
                    error=f"VERSION_MISMATCH: Ollama version {self.version} < 0.5.0 does not support structured outputs"
                )

        client = self._get_client()
        ollama_body = self._translate_request(prompt.body)
        
        try:
            response = await client.post("/api/chat", json=ollama_body)
            response.raise_for_status()
            
            openai_response = self._translate_response(response.json())
            
            # Post-inference Validation
            if response_format is not None:
                # Extract message content
                choices = openai_response.get("choices", [])
                if not choices:
                    raise ValueError("Response contains no choices")
                content = choices[0].get("message", {}).get("content", "")
                
                # Parse JSON (for both loose and strict modes)
                try:
                    parsed_json = json.loads(content)
                except json.JSONDecodeError as jde:
                    logger.warning(f"Failed to parse JSON response for {prompt.custom_id}: {jde}")
                    return CompletionResult(
                        custom_id=prompt.custom_id,
                        response=openai_response,
                        error=f"JSON_PARSE_ERROR: Response is not valid JSON: {str(jde)}"
                    )
                
                # If strict mode, validate against schema
                rf_type = response_format.get("type")
                if rf_type == "json_schema":
                    schema = response_format.get("json_schema", {}).get("schema")
                    if schema is not None:
                        try:
                            jsonschema.validate(instance=parsed_json, schema=schema)
                        except jsonschema.ValidationError as ve:
                            logger.warning(f"Schema validation failed for {prompt.custom_id}: {ve}")
                            return CompletionResult(
                                custom_id=prompt.custom_id,
                                response=openai_response,
                                error=f"SCHEMA_VIOLATION: Response JSON violates requested schema: {ve.message}"
                            )
            
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
        """Check Ollama is running via GET /api/version and GET /api/tags."""
        client = self._get_client()
        try:
            # Query version and cache it
            version_response = await client.get("/api/version", timeout=5.0)
            version_response.raise_for_status()
            self.version = version_response.json().get("version")
            if not self.version:
                logger.warning("Retrieved empty version from Ollama")
                return False
            logger.info(f"Ollama version detected: {self.version}")
                
            response = await client.get("/api/tags", timeout=5.0)
            return response.status_code == 200
        except Exception as e:
            logger.warning(f"Ollama health check or version retrieval failed: {e}")
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
        translated = {
            "model": openai_body.get("model", ""),
            "messages": openai_body.get("messages", []),
            "stream": False,
            "options": {
                "temperature": openai_body.get("temperature", 0.7),
                "num_predict": openai_body.get("max_tokens", 512),
            }
        }
        
        # Translate response_format to format per contract
        response_format = openai_body.get("response_format")
        if isinstance(response_format, dict):
            rf_type = response_format.get("type")
            if rf_type == "json_object":
                translated["format"] = "json"
            elif rf_type == "json_schema":
                schema = response_format.get("json_schema", {}).get("schema")
                if schema is not None:
                    translated["format"] = schema
                    
        return translated
        
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
