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

import httpx

from daemon.executors.base import BaseExecutor
from daemon.log import get_logger
from daemon.models import CompletionResult, PromptRequest

logger = get_logger(__name__)

# Generous timeout for inference — large prompts can take a while
_DEFAULT_TIMEOUT = 300.0  # 5 minutes


class VLLMExecutor(BaseExecutor):
    """
    Executor that forwards requests to a vLLM OpenAI-compatible server.

    The executor is stateless — each call is an independent HTTP request.
    This makes it safe to use across concurrent jobs (when we add that).

    Args:
        base_url: Base URL of the vLLM server (e.g., http://localhost:8100).
        timeout:  Per-request timeout in seconds.
    """

    def __init__(self, base_url: str, timeout: float = _DEFAULT_TIMEOUT) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        """Lazy-initialize the HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(self._timeout),
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
        Check if vLLM is reachable by hitting the /health endpoint.

        vLLM's OpenAI-compatible server exposes /health for liveness checks.
        Falls back to /v1/models if /health is not available.
        """
        client = self._get_client()

        for endpoint in ("/health", "/v1/models"):
            try:
                resp = await client.get(endpoint, timeout=10.0)
                if resp.status_code == 200:
                    logger.debug(f"vLLM health check passed via {endpoint}")
                    return True
            except Exception:
                continue

        logger.warning(f"vLLM health check failed at {self._base_url}")
        return False

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
