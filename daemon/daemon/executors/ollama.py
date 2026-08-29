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
        Execute a prompt via Ollama's endpoints.
        Routes to /api/embed if prompt.url is /v1/embeddings,
        otherwise routes to /api/chat.
        """
        client = self._get_client()

        # Embeddings first: the version gate below is about structured outputs,
        # which do not apply here. Probing /api/version for an embedding row
        # would cost a 5s timeout per prompt when the server is unreachable.
        if prompt.url == "/v1/embeddings":
            ollama_body = self._translate_embeddings_request(prompt.body)
            try:
                response = await client.post("/api/embed", json=ollama_body)
                response.raise_for_status()
                openai_response = self._translate_embeddings_response(response.json())
                return CompletionResult(
                    custom_id=prompt.custom_id,
                    response=openai_response
                )
            except Exception as e:
                logger.error(f"Ollama embedding execution failed for {prompt.custom_id}: {e}")
                return CompletionResult(
                    custom_id=prompt.custom_id,
                    error=f"EMBEDDING_FAILED: {e}"
                )

        # Lazy check version if not set (chat path only — gates structured outputs)
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

        try:
            ollama_body = self._translate_request(prompt.body)
            response = await client.post("/api/chat", json=ollama_body)
            response.raise_for_status()
            
            openai_response = self._translate_response(response.json())
            
            # Post-inference Validation
            if response_format is not None:
                # Extract message content
                choices = openai_response.get("choices", [])
                if not choices:
                    logger.warning(f"Ollama response contains no choices for {prompt.custom_id}")
                    return CompletionResult(
                        custom_id=prompt.custom_id,
                        response=openai_response,
                        error="EMPTY_RESPONSE: Response contains no choices"
                    )
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
            
    # ── OpenAI → Ollama parameter mappings ────────────────────
    #
    # Ollama nests most sampling parameters under an "options"
    # object rather than top-level. This mapping is the single
    # source of truth for which OpenAI keys map to which Ollama
    # options keys. See docs/reference/openai-compatibility.md for the
    # full matrix.

    _OPTION_TRANSLATIONS = {
        "top_p":              "top_p",
        "top_k":              "top_k",
        "stop":               "stop",
        "seed":               "seed",
        "frequency_penalty":  "frequency_penalty",
        "presence_penalty":   "presence_penalty",
    }

    # Parameters that have NO Ollama equivalent. Each is logged
    # with its reason — no silent drops, that is the whole point
    # of issue #39.
    _UNSUPPORTED_PARAMS = {
        "logprobs":     "Ollama does not support logprobs",
        "top_logprobs": "Ollama does not support top_logprobs",
        "tool_choice":  ("Ollama does not support tool_choice — tool "
                         "selection is determined by the model from "
                         "the tools list"),
    }

    def _translate_request(self, openai_body: dict) -> dict:
        """OpenAI chat format -> Ollama chat format.

        Translates every supported sampling parameter into Ollama's
        ``options`` object, warns on unsupported parameters (never
        drops silently), and rejects parameters that are fundamentally
        incompatible (``n > 1``, ``stream``).

        Raises:
            ValueError: if ``n > 1`` (Ollama produces exactly 1
                completion per request).  Callers must catch this
                and convert to a CompletionResult.
        """
        translated = {
            "model": openai_body.get("model", ""),
            "messages": openai_body.get("messages", []),
            "stream": False,
            "options": {
                "temperature": openai_body.get("temperature", 0.7),
                "num_predict": openai_body.get("max_tokens", 512),
            }
        }

        # ── Sampling parameters → options.* ──────────────────
        for openai_key, ollama_key in self._OPTION_TRANSLATIONS.items():
            if openai_key in openai_body:
                value = openai_body[openai_key]
                # OpenAI permits a bare string for `stop`; Ollama's
                # options decoder only accepts an array.
                if openai_key == "stop" and isinstance(value, str):
                    value = [value]
                translated["options"][ollama_key] = value

        # ── Unsupported parameters → warn-and-drop ───────────
        for param, reason in self._UNSUPPORTED_PARAMS.items():
            if param in openai_body:
                logger.warning("Parameter '%s' dropped: %s", param, reason)

        # ── n > 1 → reject ───────────────────────────────────
        # Ollama produces exactly 1 completion per request.
        # n=1 is fine (it is the default); n>1 is unsupported.
        n_value = openai_body.get("n")
        if n_value is not None and n_value != 1:
            raise ValueError(
                f"UNSUPPORTED_PARAMETER: n={n_value} is not supported by "
                f"Ollama (exactly 1 completion per request)"
            )

        # ── tools → top-level (Ollama native since ~0.3) ─────
        # `tool_choice: "none"` means the model may NOT call tools.
        # Ollama has no tool_choice, so honour it by withholding the
        # tool list rather than dropping the caller's intent.
        if "tools" in openai_body and openai_body.get("tool_choice") != "none":
            translated["tools"] = openai_body["tools"]

        # ── response_format → format (existing, from #41) ────
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
        choices = []
        if "message" in ollama_response and ollama_response["message"]:
            choices.append({
                "index": 0,
                "message": ollama_response.get("message", {}),
                "finish_reason": "stop" if ollama_response.get("done") else "length",
            })
        return {
            "choices": choices,
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

    def _translate_embeddings_request(self, openai_body: dict) -> dict:
        """OpenAI embeddings format -> Ollama embed format."""
        translated = {
            "model": openai_body.get("model", ""),
            "input": openai_body.get("input", "")
        }
        if "truncate" in openai_body:
            translated["truncate"] = openai_body["truncate"]
        return translated

    def _translate_embeddings_response(self, ollama_response: dict) -> dict:
        """Ollama embed response -> OpenAI-compatible response format.

        `/api/embed` (Ollama 0.3+) returns `embeddings`: a list of vectors,
        one per input. The deprecated `/api/embeddings` returns `embedding`:
        a single flat vector. We only call the former, but the latter is
        handled because a proxy or an older server on the Ollama port can
        still answer in the legacy shape — and silently returning zero
        vectors would be worse than normalising it.
        """
        embeddings = ollama_response.get("embeddings")
        if embeddings is None and "embedding" in ollama_response:
            single_emb = ollama_response["embedding"]
            # Flat vector (legacy) -> wrap. Already-nested -> take as-is.
            if isinstance(single_emb, list) and len(single_emb) > 0 and not isinstance(single_emb[0], list):
                embeddings = [single_emb]
            else:
                embeddings = single_emb
        if embeddings is None:
            embeddings = []

        data = []
        for idx, emb in enumerate(embeddings):
            data.append({
                "object": "embedding",
                "embedding": emb,
                "index": idx
            })
        
        prompt_tokens = ollama_response.get("prompt_eval_count", 0)
        
        return {
            "object": "list",
            "data": data,
            "model": ollama_response.get("model", ""),
            "usage": {
                "prompt_tokens": prompt_tokens,
                "total_tokens": prompt_tokens
            }
        }
