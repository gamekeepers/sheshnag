"""
llama.cpp executor — sends prompts to a provider-run `llama-server`.

`llama-server` speaks the OpenAI-compatible API, so each prompt's `body`
forwards to the matching endpoint much as it does for vLLM.

Architecture note:
    The daemon attaches to a server the provider started; it never
    launches, restarts or tunes one. Whatever `llama-server` was given on
    its command line — the GPU/RAM split via `--n-gpu-layers`, the slot
    count, whether embeddings are enabled — is fixed for the lifetime of
    that process and is the provider's decision.

    A server started with `--models-dir` is a router: it answers for every
    GGUF in that directory and loads them on demand, so which model is in
    memory changes over the process lifetime. Without it, one process holds
    one model until it exits.

    This is what makes llama.cpp worth supporting: `--n-gpu-layers` puts
    as many layers on the GPU as fit and streams the rest from system RAM,
    so a machine whose card cannot hold a model can still serve it.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from daemon.executors.base import BaseExecutor
from daemon.log import get_logger
from daemon.models import CompletionResult, PromptRequest

logger = get_logger(__name__)


class LlamaCppExecutor(BaseExecutor):
    """
    Executor that forwards requests to a `llama-server` instance.

    Availability and residence are separate questions. `list_models()`
    returns every name the server answers to; `list_running_models()`
    returns the subset held in memory. They differ only under a router
    server, where entries load and unload on demand.

    Args:
        base_url:       Base URL of the llama-server (e.g. http://localhost:8080).
        timeout:        Per-request timeout in seconds. Hybrid and CPU-only
                        inference is slow — this wants to be generous.
        max_concurrent: Sizes the HTTP connection pool. The server's own
                        slot count is reported by health_check(); a caller
                        exceeding it gains queueing, not throughput.
        models_dir:     The directory the router serves. The HTTP API names
                        models but never locates them, and identity is the
                        file's hash — so without this the daemon can describe
                        a model it cannot identify.
    """

    runtime_name: str = "llamacpp"

    # One hash per beat. A 17 GB read takes ~90s even from local disk, and
    # inventory runs on the heartbeat: several per beat would stall the beat
    # that proves the worker alive. A directory converges over a few beats.
    HASHES_PER_BEAT = 1

    def __init__(
        self,
        base_url: str,
        timeout: float = 300.0,
        max_concurrent: int = 8,
        models_dir: Optional[str] = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._max_concurrent = max_concurrent
        self._models_dir = Path(models_dir) if models_dir else None
        # name -> {"sha256", "size", "mtime", "source_ref", "source_revision"}
        self._identity: Dict[str, dict] = {}
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
            for name in self._entry_names(entry):
                if name not in names:
                    names.append(name)
        # Leave a previously-known set in place on a failed read: forgetting
        # it would silently disable the mismatch guard exactly when the
        # server is unhealthy.
        if names:
            self._served = set(names)
        return names

    @staticmethod
    def _entry_names(entry: dict) -> List[str]:
        """An entry's id and aliases, in that order, without duplicates."""
        names: List[str] = []
        for name in [entry.get("id"), *(entry.get("aliases") or [])]:
            if name and name not in names:
                names.append(name)
        return names

    async def list_models(self) -> List[str]:
        """Every name this server answers to, loaded or not."""
        return await self._refresh_served()

    async def list_running_models(self) -> List[str]:
        """
        The names actually held in memory.

        A router server (`--models-dir`) serves a whole directory and loads
        entries on demand, reporting each one's state as `status.value`; only
        `loaded` occupies memory. A single-model server omits `status`
        entirely, and its one model is resident for the process lifetime.
        """
        served: List[str] = []
        running: List[str] = []
        for entry in await self._models_payload():
            names = self._entry_names(entry)
            for name in names:
                if name not in served:
                    served.append(name)
            status = entry.get("status")
            if isinstance(status, dict) and status.get("value") != "loaded":
                continue
            running.extend(n for n in names if n not in running)
        # The heartbeat calls this, and it is the only listing that runs on a
        # schedule: refresh the guard's cache here or a provider who restarts
        # llama-server on a different GGUF keeps being rejected against the
        # names the old process held.
        if served:
            self._served = set(served)
        return running

    def _gguf_path(self, name: str) -> Optional[Path]:
        """The file behind a served name.

        A router reports each file's stem as the model id, so the mapping is
        the stem plus `.gguf`. Anything else in the directory is not what the
        server answered for.
        """
        if self._models_dir is None:
            return None
        candidate = self._models_dir / f"{name}.gguf"
        return candidate if candidate.is_file() else None

    @staticmethod
    def _sidecar_path(gguf: Path) -> Path:
        return gguf.with_suffix(gguf.suffix + ".json")

    @staticmethod
    def _hash_file(path: Path) -> str:
        digest = hashlib.sha256()
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(8 * 1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def _read_sidecar(self, gguf: Path, size: int) -> Optional[dict]:
        """The staged identity of this file, if it is still about this file.

        `stage-models.sh` writes the hash it already computed at the source,
        so a worker that received a file never has to read it back. The size
        check is what stops a stale sidecar surviving a re-staged model.
        """
        try:
            data = json.loads(self._sidecar_path(gguf).read_text())
        except (OSError, ValueError):
            return None
        if not data.get("sha256") or data.get("size") not in (None, size):
            return None
        return data

    def _write_sidecar(self, gguf: Path, record: dict) -> None:
        try:
            self._sidecar_path(gguf).write_text(json.dumps(record))
        except OSError as exc:
            logger.debug(f"Could not write sidecar for {gguf.name}: {exc}")

    async def _identify(self, name: str, budget: List[int]) -> dict:
        """sha256 and provenance for one served model.

        Cached on size and mtime, so a re-staged file is re-read and an
        untouched one is read once per process. `budget` is decremented when
        this call had to hash, which is how the per-beat cap is enforced.
        """
        gguf = self._gguf_path(name)
        if gguf is None:
            return {}
        try:
            stat = gguf.stat()
        except OSError:
            return {}

        cached = self._identity.get(name)
        if cached and cached["size"] == stat.st_size and cached["mtime"] == stat.st_mtime:
            return cached

        staged = self._read_sidecar(gguf, stat.st_size)
        if staged:
            record = {
                "sha256": staged["sha256"],
                "size": stat.st_size,
                "mtime": stat.st_mtime,
                "source_ref": staged.get("source_ref"),
                "source_revision": staged.get("source_revision"),
            }
            self._identity[name] = record
            return record

        if budget[0] <= 0:
            return {}
        budget[0] -= 1
        logger.info(f"Hashing {gguf.name} to identify it (no sidecar)")
        try:
            sha = await asyncio.to_thread(self._hash_file, gguf)
        except OSError as exc:
            logger.warning(f"Could not hash {gguf.name}: {exc}")
            return {}
        record = {
            "sha256": sha, "size": stat.st_size, "mtime": stat.st_mtime,
            "source_ref": None, "source_revision": None,
        }
        self._identity[name] = record
        self._write_sidecar(gguf, {"sha256": sha, "size": stat.st_size})
        return record

    async def inventory(self) -> List[dict]:
        """
        The served artifact, with what `/v1/models` knows about it.

        The hash is the artifact's identity — the catalogue pins the sha256
        of the weights file, and an Ollama model layer's digest is that same
        number — so a staged GGUF can be confirmed against its public source
        rather than matched on a filename a provider chose. Without it the
        backend falls back to name matching, and every variant of one model
        needs a hand-written catalogue row.

        `sha256` stays absent when the directory is unknown or the file has
        not been read yet; the row is still reported, because a model that
        can be served should be visible while it is being identified.
        """
        budget = [self.HASHES_PER_BEAT]
        items: List[dict] = []
        for entry in await self._models_payload():
            name = entry.get("id")
            if not name:
                continue
            meta: Dict[str, Any] = entry.get("meta") or {}
            identity = await self._identify(name, budget)
            details = {
                "format": "gguf",
                "quantization_level": meta.get("ftype"),
                "parameter_count": meta.get("n_params"),
                "context_length": meta.get("n_ctx_train"),
                "embedding_length": meta.get("n_embd"),
            }
            if identity.get("source_ref"):
                details["source_ref"] = identity["source_ref"]
            if identity.get("source_revision"):
                details["source_revision"] = identity["source_revision"]
            items.append({
                "local_name": name,
                "sha256": identity.get("sha256"),
                "size_bytes": identity.get("size") or meta.get("size"),
                "details": details,
            })
        return self.tag_inventory(items)

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
