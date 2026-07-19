"""
vLLM executor — sends prompts to a local vLLM OpenAI-compatible server.

vLLM exposes an OpenAI-compatible API when started with `vllm serve`.
This executor simply forwards each prompt's `body` to the appropriate
endpoint and wraps the response.

Architecture note:
    The daemon does NOT start or manage the vLLM process. That is the
    responsibility of the runtime team (@Akshay / @Ankush). The daemon
    only assumes vLLM is reachable at the configured URL.
"""

from __future__ import annotations

from typing import Optional, Set

import httpx

from daemon.executors.base import BaseExecutor
from daemon.log import get_logger
from daemon.models import CompletionResult, PromptRequest

logger = get_logger(__name__)


class VLLMExecutor(BaseExecutor):
    """
    Executor that forwards requests to a vLLM OpenAI-compatible server.

    The executor is stateless — each call is an independent HTTP request.
    This makes it safe to use across concurrent jobs (when we add that).

    Args:
        base_url:          Base URL of the vLLM server (e.g., http://localhost:8100).
        timeout:           Per-request timeout in seconds (configurable via DaemonConfig.inference_timeout).
        supported_models:  Optional list of models this worker supports. If provided,
                           health_check() will verify these models are loaded in vLLM.
    """

    def __init__(
        self,
        base_url: str,
        timeout: float = 300.0,
        supported_models: Optional[list[str]] = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._supported_models: Set[str] = set(supported_models) if supported_models else set()
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        """
        Lazy-initialize the HTTP client with connection pooling.

        Connection pooling is important for a daemon that sends
        many sequential requests to the same vLLM server.
        """
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(self._timeout),
                limits=httpx.Limits(
                    max_connections=10,
                    max_keepalive_connections=5,
                ),
            )
        return self._client

    async def execute(self, prompt: PromptRequest) -> CompletionResult:
        """
        Send a single prompt to vLLM and return the result.

        Errors are caught and returned in the CompletionResult rather
        than raised. This ensures one bad prompt doesn't kill the
        entire batch.

        Args:
            prompt: Parsed JSONL row with the OpenAI-format request body.

        Returns:
            CompletionResult with the vLLM response or error details.
        """
        client = self._get_client()

        try:
            response = await client.request(
                method=prompt.method,
                url=prompt.url,
                json=prompt.body,
            )
            response.raise_for_status()

            response_body = response.json()
            logger.debug(
                f"Prompt {prompt.custom_id} completed — "
                f"tokens: {response_body.get('usage', {})}"
            )

            return CompletionResult(
                custom_id=prompt.custom_id,
                response=response_body,
            )

        except httpx.TimeoutException:
            error_msg = (
                f"Timeout after {self._timeout}s for prompt {prompt.custom_id}"
            )
            logger.warning(error_msg)
            return CompletionResult(
                custom_id=prompt.custom_id, error=error_msg
            )

        except httpx.HTTPStatusError as exc:
            error_msg = (
                f"HTTP {exc.response.status_code} for prompt {prompt.custom_id}: "
                f"{exc.response.text[:500]}"
            )
            logger.warning(error_msg)
            return CompletionResult(
                custom_id=prompt.custom_id, error=error_msg
            )

        except Exception as exc:
            error_msg = f"Unexpected error for prompt {prompt.custom_id}: {exc}"
            logger.error(error_msg, exc_info=True)
            return CompletionResult(
                custom_id=prompt.custom_id, error=error_msg
            )

    async def health_check(self) -> bool:
        """
        Check if vLLM is reachable and expected models are loaded.

        Two-phase check:
            1. Hit /health (or /v1/models as fallback) for liveness.
            2. If supported_models is configured, verify those models
               appear in /v1/models response body — not just that the
               endpoint returns 200.
        """
        client = self._get_client()

        # Phase 1: Basic liveness check
        is_alive = False
        for endpoint in ("/health", "/v1/models"):
            try:
                resp = await client.get(endpoint, timeout=10.0)
                if resp.status_code == 200:
                    logger.debug(f"vLLM health check passed via {endpoint}")
                    is_alive = True
                    break
            except Exception:
                continue

        if not is_alive:
            logger.warning(f"vLLM health check failed at {self._base_url}")
            return False

        # Phase 2: Verify expected models are loaded (if configured)
        if self._supported_models:
            try:
                resp = await client.get("/v1/models", timeout=10.0)
                if resp.status_code == 200:
                    data = resp.json()
                    loaded_models = {m["id"] for m in data.get("data", [])}
                    missing = self._supported_models - loaded_models
                    if missing:
                        logger.warning(
                            f"Expected models not loaded in vLLM: {missing}. "
                            f"Loaded: {loaded_models}"
                        )
                        return False
                    logger.debug(f"All expected models confirmed loaded: {self._supported_models}")
            except Exception as exc:
                logger.warning(f"Could not verify loaded models: {exc}")
                # Don't fail health check if we can't verify models
                # but the server is alive — it might just be loading

        return True

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
