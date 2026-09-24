"""
llama.cpp executor — sends prompts to a provider-run `llama-server`.

`llama-server` speaks the OpenAI-compatible API, so each prompt's `body`
forwards to the matching endpoint much as it does for vLLM.

Architecture note:
    The daemon attaches to a server the provider started; it never
    launches, restarts or tunes one. Whatever `llama-server` was given on
    its command line — the model, the GPU/RAM split via `--n-gpu-layers`,
    the slot count, whether embeddings are enabled — is fixed for the
    lifetime of that process and is the provider's decision.

    This is what makes llama.cpp worth supporting: `--n-gpu-layers` puts
    as many layers on the GPU as fit and streams the rest from system RAM,
    so a machine whose card cannot hold a model can still serve it.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx

from daemon.executors.base import BaseExecutor
from daemon.log import get_logger
from daemon.models import CompletionResult, PromptRequest

logger = get_logger(__name__)


class LlamaCppExecutor(BaseExecutor):
    """
    Executor that forwards requests to a `llama-server` instance.

    One server process serves exactly one model, held for the process
    lifetime. Availability and residence are therefore the same question,
    and both listing methods return the same single-entry list.

    Args:
        base_url:       Base URL of the llama-server (e.g. http://localhost:8080).
        timeout:        Per-request timeout in seconds. Hybrid and CPU-only
                        inference is slow — this wants to be generous.
        max_concurrent: Sizes the HTTP connection pool. The server's own
                        slot count is reported by health_check(); a caller
                        exceeding it gains queueing, not throughput.
    """

    runtime_name: str = "llamacpp"

    def __init__(
        self,
        base_url: str,
        timeout: float = 300.0,
        max_concurrent: int = 8,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._max_concurrent = max_concurrent
        self._client: httpx.AsyncClient | None = None
        self.version: Optional[str] = None      # build_info, from health_check()
        self.total_slots: Optional[int] = None  # server-side concurrency
        self.model_path: Optional[str] = None   # GGUF the server was given
        self.supports_embeddings: bool = False
        # Served names, cached by health_check() and by list_models(). Used to
        # reject a prompt aimed at a model this server does not hold; None
        # means "not yet known", which is not the same as "serves nothing".
        self._served: Optional[set] = None

    def _get_client(self) -> httpx.AsyncClient:
        """Lazy HTTP client with a pool wide enough for the configured fan-out."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(self._timeout),
                limits=httpx.Limits(
                    max_connections=self._max_concurrent + 4,
                    max_keepalive_connections=self._max_concurrent + 4,
                ),
            )
        return self._client

    # ── Model guard ──────────────────────────────────────────────
    #
    # `llama-server` ignores `body.model`: a request naming a model it does
    # not hold is answered by the model it does hold, with HTTP 200 and the
    # served name echoed in the response. Unguarded, a batch pinned to one
    # artifact can be silently answered by another — wrong output that
    # nothing downstream can detect.
    #
    # Checked twice. Before the request when the served set is known, so the
    # mismatch costs no inference; after it against the echoed name, which
    # needs no cache and catches a server whose model changed under us.

    def _mismatch(self, requested: Optional[str]) -> Optional[str]:
        """Error text when `requested` is a model this server does not serve."""
        if not requested or self._served is None:
            return None
        if requested in self._served:
            return None
        return (
            f"MODEL_MISMATCH: llama-server holds {sorted(self._served)}, "
            f"not {requested!r}. It would answer from the loaded model."
        )

    async def execute(self, prompt: PromptRequest) -> CompletionResult:
        """
        Send one prompt to llama-server and return the result.

        Errors are returned in the CompletionResult, never raised, so one
        bad row cannot end the batch.
        """
        requested = prompt.body.get("model")

        pre = self._mismatch(requested)
        if pre:
            logger.warning("Prompt %s rejected: %s", prompt.custom_id, pre)
            return CompletionResult(custom_id=prompt.custom_id, error=pre)

        client = self._get_client()
        try:
            response = await client.request(
                method=prompt.method,
                url=prompt.url,
                json=dict(prompt.body),
            )
            response.raise_for_status()
            body = response.json()

            served = body.get("model")
            if requested and served and served != requested:
                error = (
                    f"MODEL_MISMATCH: asked for {requested!r}, "
                    f"llama-server answered as {served!r}"
                )
                logger.error("Prompt %s: %s", prompt.custom_id, error)
                return CompletionResult(custom_id=prompt.custom_id, error=error)

            logger.debug(
                "Prompt %s completed — tokens: %s",
                prompt.custom_id, body.get("usage", {}),
            )
            return CompletionResult(custom_id=prompt.custom_id, response=body)

        except httpx.TimeoutException:
            error = (
                f"Timeout after {self._timeout}s for prompt {prompt.custom_id}"
            )
            logger.warning(error)
            return CompletionResult(custom_id=prompt.custom_id, error=error)

        except httpx.HTTPStatusError as exc:
            # 501 is the server saying a capability was never enabled at
            # launch — embeddings without `--embeddings`, most often. Naming
            # it separates "the provider did not start this" from a bad row.
            if exc.response.status_code == 501:
                error = (
                    f"UNSUPPORTED_ENDPOINT: llama-server was not started with "
                    f"support for {prompt.url} — {exc.response.text[:300]}"
                )
            else:
                error = (
                    f"HTTP {exc.response.status_code} for prompt "
                    f"{prompt.custom_id}: {exc.response.text[:500]}"
                )
            logger.warning(error)
            return CompletionResult(custom_id=prompt.custom_id, error=error)

        except Exception as exc:
            error = f"Unexpected error for prompt {prompt.custom_id}: {exc}"
            logger.error(error, exc_info=True)
            return CompletionResult(custom_id=prompt.custom_id, error=error)

    def capabilities(self) -> Dict[str, bool]:
        # llama-server answers OpenAI-shaped logprobs on /v1/chat/completions
        # and /v1/completions (llama.cpp PR #10783) but has no `echo`.
        return {"logprobs": True, "completions": True, "prompt_scoring": False}

    async def health_check(self) -> bool:
        """
        Liveness, plus the server facts the rest of the daemon needs.

        `/props` carries the build string, the GGUF path and the slot count;
        there is no `/version` endpoint. `/health` is the liveness signal.
        Neither the properties nor the served-name cache can fail the check
        on their own — a server that answers `/health` is usable.
        """
        client = self._get_client()

        try:
            resp = await client.get("/props", timeout=10.0)
            if resp.status_code == 200:
                props = resp.json()
                self.version = props.get("build_info")
                self.model_path = props.get("model_path")
                self.total_slots = props.get("total_slots")
                logger.info(
                    "llama-server %s — %s slot(s), model %s",
                    self.version, self.total_slots, self.model_path,
                )
        except Exception as exc:
            logger.warning("Could not read llama-server properties: %s", exc)

        try:
            resp = await client.get("/health", timeout=10.0)
            if resp.status_code != 200:
                logger.warning(
                    "llama-server health check returned %s at %s",
                    resp.status_code, self._base_url,
                )
                return False
        except Exception as exc:
            logger.warning(
                "llama-server unreachable at %s: %s", self._base_url, exc,
            )
            return False

        await self._refresh_served()
        return True

    async def _models_payload(self) -> List[dict]:
        """`/v1/models` OpenAI-shaped entries, or [] when unavailable."""
        try:
            client = self._get_client()
            resp = await client.get("/v1/models", timeout=10.0)
            resp.raise_for_status()
            return resp.json().get("data", []) or []
        except Exception as exc:
            logger.warning("Could not list llama-server models: %s", exc)
            return []

    async def _refresh_served(self) -> List[str]:
        """Served names, refreshing the guard's cache as a side effect."""
        names: List[str] = []
        for entry in await self._models_payload():
            for name in [entry.get("id"), *(entry.get("aliases") or [])]:
                if name and name not in names:
                    names.append(name)
        # Leave a previously-known set in place on a failed read: forgetting
        # it would silently disable the mismatch guard exactly when the
        # server is unhealthy.
        if names:
            self._served = set(names)
        return names

    async def list_models(self) -> List[str]:
        """Names this server answers to — its model plus any alias."""
        return await self._refresh_served()

    async def list_running_models(self) -> List[str]:
        """One process, one model, held for its lifetime: served is resident."""
        return await self._refresh_served()

    async def inventory(self) -> List[dict]:
        """
        The served artifact, with what `/v1/models` knows about it.

        `sha256` is None: identifying the artifact means hashing a
        multi-gigabyte GGUF, and the catalogue currently pins Ollama
        manifest digests that no file hash can equal, so a hash here would
        quarantine the row rather than match it. Name matching is what the
        backend falls back to, and `meta` still carries enough to describe
        the artifact in the portal.
        """
        items: List[dict] = []
        for entry in await self._models_payload():
            name = entry.get("id")
            if not name:
                continue
            meta: Dict[str, Any] = entry.get("meta") or {}
            items.append({
                "local_name": name,
                "sha256": None,
                "size_bytes": meta.get("size"),
                "details": {
                    "format": "gguf",
                    "quantization_level": meta.get("ftype"),
                    "parameter_count": meta.get("n_params"),
                    "context_length": meta.get("n_ctx_train"),
                    "embedding_length": meta.get("n_embd"),
                },
            })
        return self.tag_inventory(items)

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
