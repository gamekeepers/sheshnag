import asyncio
import json
import logging
import os
import socket
import time
from pathlib import Path
from urllib.parse import urlparse
from typing import Dict, Optional, List, Callable, Awaitable

import httpx
import jsonschema

from daemon.executors.base import BaseExecutor
from daemon.models import CompletionResult, PromptRequest

logger = logging.getLogger(__name__)


def _split_tokens(total: int, parts: int, index: int) -> int:
    """Share a coalesced chunk's token count across its rows, losing none.

    Ollama bills one number for the whole /api/embed call, but each row is
    reported separately. Plain floor division drops up to parts-1 tokens per
    chunk, which makes every batch rollup systematically low. The remainder
    goes to the first rows instead, so the parts always sum back to total.
    """
    if parts <= 0:
        return 0
    base, remainder = divmod(int(total), parts)
    return base + (1 if index < remainder else 0)

# App-server signatures that mean something other than Ollama is answering on
# this port. Ollama itself sends no Server header, and a reverse proxy in front
# of a working Ollama is fine — so we only warn on servers that host
# applications directly. See issue #80 (aiohttp app squatting on :11434).
NON_OLLAMA_SERVER_SIGNATURES = (
    "aiohttp", "uvicorn", "gunicorn", "werkzeug", "python/",
    "kestrel", "jetty", "tomcat", "coyote", "express",
)

# How long a *failed* version probe is cached before it is retried. Issue #83
# is about not paying a 5s connect timeout on every prompt of a 10k batch, so
# the failure has to be cached — but not for the life of the process. The
# daemon is explicitly allowed to start before Ollama is up (see
# Worker._wait_for_executor), and structured outputs have to start working
# once it does. One probe per minute is ~0.08% of the pre-fix cost.
VERSION_PROBE_RETRY_SECONDS = 60.0

# /api/chat returns per-token log-probabilities from this release on.
LOGPROBS_MIN_VERSION = (0, 12, 11)

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
    
    #: Ollama's /api/embed accepts a list of inputs in one request.
    embedding_chunk_size: int = 64
    runtime_name: str = "ollama"

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        timeout: float = 300.0,
        max_concurrent: int = 8,
        models_dir: Optional[str] = None,
    ):
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._max_concurrent = max_concurrent
        self._models_dir = models_dir
        # sha256 (or name when hash-less) -> details dict. Details never
        # change for a given artifact, so /api/show is paid once per model,
        # not once per heartbeat.
        self._details_cache: dict = {}
        self._details_failed_at: dict = {}            # key -> monotonic time of last failed lookup
        self._details_api_failed_at: Optional[float] = None
        self._client: Optional[httpx.AsyncClient] = None
        self.version: Optional[str] = None
        self._server_header_warned = False
        self._version_probed_at: Optional[float] = None
        
    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            # Sized from the pool: one connection per in-flight prompt, plus
            # headroom for the health/version probes that run alongside them.
            pool_limit = self._max_concurrent + 4
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(self._timeout),
                limits=httpx.Limits(
                    max_connections=pool_limit,
                    max_keepalive_connections=pool_limit,
                ),
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
        if prompt.url == "/v1/completions":
            # Ollama's /api/generate cannot echo prompt log-probabilities, and
            # the scheduler never routes these rows here; this is the backstop.
            return CompletionResult(
                custom_id=prompt.custom_id,
                error="UNSUPPORTED_ENDPOINT: /v1/completions is not served by the Ollama runtime",
            )

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

        # Lazy version probe (chat path only — gates structured outputs).
        # A successful probe is cached for the process; a failed one is cached
        # only for VERSION_PROBE_RETRY_SECONDS so a recovering server heals.
        if self._should_probe_version():
            await self.health_check()

        response_format = prompt.body.get("response_format")
        
        # Version Gating: Refuse JSON mode if Ollama version < 0.5.0 (or undetermined)
        if response_format is not None:
            if self.version is None:
                logger.error("Ollama version undetermined. Server might be unreachable.")
                return CompletionResult(
                    custom_id=prompt.custom_id,
                    error=(
                        "OLLAMA_UNREACHABLE: could not determine Ollama version — "
                        "server may be down. The failed probe is cached; it retries "
                        f"every {VERSION_PROBE_RETRY_SECONDS:.0f}s."
                    )
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
            choices = openai_response.get("choices", [])
            if not choices:
                logger.warning(f"Ollama response contains no choices for {prompt.custom_id}")
                return CompletionResult(
                    custom_id=prompt.custom_id,
                    response=openai_response,
                    error="EMPTY_RESPONSE: Response contains no choices"
                )

            if response_format is not None:
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
    
    def _should_probe_version(self) -> bool:
        """Whether to probe /api/version before serving this prompt.

        A known version is cached for the life of the executor. An unknown one
        is re-probed at most once per VERSION_PROBE_RETRY_SECONDS, so a server
        that was down at startup stops blocking structured outputs when it
        comes back.
        """
        if self.version is not None:
            return False
        if self._version_probed_at is None:
            return True
        return (time.monotonic() - self._version_probed_at) >= VERSION_PROBE_RETRY_SECONDS

    def can_coalesce_embedding(self, prompt: PromptRequest) -> bool:
        """Only single-string inputs may share an /api/embed request.

        A list-valued input is a valid OpenAI shape and nothing upstream
        rejects it, but nesting it in a batched body either fails the whole
        chunk or returns one embedding per *flattened* element — which
        desynchronises the data[j] -> chunk[j] fan-out and hands later
        custom_ids somebody else's vector. execute() handles these rows
        correctly one at a time.
        """
        return (
            prompt.url == "/v1/embeddings"
            and isinstance(prompt.body.get("input"), str)
        )

    async def batch_execute(self, prompts: List[PromptRequest]) -> List[CompletionResult]:
        """
        Execute a batch of prompts, coalescing embeddings into single
        /api/embed calls.

        Chat prompts fall through to per-prompt execute(); the worker's pool
        supplies their concurrency. Embeddings are chunked because Ollama
        accepts a list of inputs in one request, which removes one HTTP round
        trip per row.
        """
        embedding_prompts = [p for p in prompts if self.can_coalesce_embedding(p)]
        per_prompt = [p for p in prompts if not self.can_coalesce_embedding(p)]

        results_by_id = {}

        for p in per_prompt:
            results_by_id[p.custom_id] = await self.execute(p)

        CHUNK_SIZE = self.embedding_chunk_size
        client = self._get_client()
        for i in range(0, len(embedding_prompts), CHUNK_SIZE):
            chunk = embedding_prompts[i:i + CHUNK_SIZE]
            inputs = [p.body.get("input", "") for p in chunk]
            model = chunk[0].body.get("model", "")

            coalesced_body = {"model": model, "input": inputs}
            if "truncate" in chunk[0].body:
                coalesced_body["truncate"] = chunk[0].body["truncate"]

            try:
                response = await client.post("/api/embed", json=coalesced_body)
                response.raise_for_status()

                openai_response = self._translate_embeddings_response(response.json())

                # Fan the single response back out, one CompletionResult per row.
                for j, p in enumerate(chunk):
                    data = openai_response.get("data", [])
                    if j < len(data):
                        data_item = dict(data[j])
                        data_item["index"] = 0
                        usage = openai_response.get("usage", {})
                        per_row_response = {
                            "object": "list",
                            "data": [data_item],
                            "model": openai_response.get("model", model),
                            "usage": {
                                "prompt_tokens": _split_tokens(
                                    usage.get("prompt_tokens", 0), len(chunk), j
                                ),
                                "total_tokens": _split_tokens(
                                    usage.get("total_tokens", 0), len(chunk), j
                                ),
                            },
                        }
                        results_by_id[p.custom_id] = CompletionResult(
                            custom_id=p.custom_id, response=per_row_response
                        )
                    else:
                        # Fewer vectors came back than rows went out. Ask for
                        # this one on its own rather than calling it failed.
                        logger.warning(
                            "Ollama returned no vector for %s in a coalesced "
                            "chunk — retrying it individually", p.custom_id,
                        )
                        results_by_id[p.custom_id] = await self.execute(p)

            except Exception as e:
                # Ollama rejects the entire request if any single input is bad
                # (an empty string, one input over the context limit), so a
                # chunk failure says nothing about the other 63 rows. Retry
                # them one at a time rather than failing them all: coalescing
                # is a throughput optimisation and must not cost isolation the
                # per-prompt path had.
                logger.warning(
                    "Ollama coalesced embedding call failed (%s) — retrying "
                    "%d row(s) individually", e, len(chunk),
                )
                for p in chunk:
                    results_by_id[p.custom_id] = await self.execute(p)

        return [results_by_id[p.custom_id] for p in prompts]

    async def health_check(self) -> bool:
        """Check Ollama is running via GET /api/version and GET /api/tags."""
        self._version_probed_at = time.monotonic()
        client = self._get_client()
        try:
            # Query version and cache it
            version_response = await client.get("/api/version", timeout=5.0)

            server_header = version_response.headers.get("server", "")
            if server_header and not self._server_header_warned:
                # Latched: health_check() runs per startup retry and per prompt
                # while version is unset, so log the header diagnosis only once.
                self._server_header_warned = True
                logger.info(f"Server header from {self._base_url}: {server_header}")
                header_lc = server_header.lower()
                if any(sig in header_lc for sig in NON_OLLAMA_SERVER_SIGNATURES):
                    hint = (
                        "Inference may fail even though metadata endpoints respond."
                        if version_response.is_success
                        else f"/api/version returned HTTP {version_response.status_code}."
                    )
                    logger.warning(
                        f"Detected non-Ollama application server on {self._base_url} "
                        f"(Server: {server_header}). {hint}"
                    )

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

    async def list_running_models(self) -> List[str]:
        """Models loaded in VRAM via GET /api/ps.

        /api/tags answers a different question — every model on disk — so
        using it here reports an idle machine as fully loaded.
        """
        client = self._get_client()
        try:
            response = await client.get("/api/ps", timeout=5.0)
            response.raise_for_status()
            return [
                m["name"] for m in response.json().get("models", []) if m.get("name")
            ]
        except Exception as e:
            logger.debug(f"Could not list running Ollama models: {e}")
            return []

    # ── On-disk inventory (registry identity) ─────────────────

    def _is_local_server(self) -> bool:
        """Whether ollama_url names this machine, so a local store can be its store."""
        host = (urlparse(self._base_url).hostname or "").lower()
        return host in {"localhost", "127.0.0.1", "::1", "", socket.gethostname().lower()}

    def _resolve_models_dir(self) -> Optional[Path]:
        """The Ollama models dir whose manifests we can actually read.

        Order: explicit config, $OLLAMA_MODELS, the user store
        (~/.ollama/models), the systemd service store
        (/usr/share/ollama/.ollama/models). The two default stores are
        only considered for a local server — a remote ollama_url makes any
        path on this box the wrong store. Read-only; when none is readable
        (daemon runs as a different user), inventory() degrades to
        /api/tags names — never escalate privileges to win the read.
        """
        # A local manifests tree describes a local server. When ollama_url
        # points elsewhere, the tree on this box belongs to a different
        # Ollama and its hashes would be attributed to the remote one, so
        # only an explicit models_dir (the operator saying "these are the
        # same store") is honoured.
        explicit = self._models_dir or os.environ.get("OLLAMA_MODELS")
        candidates = [explicit]
        if self._is_local_server():
            candidates += [
                os.path.expanduser("~/.ollama/models"),
                "/usr/share/ollama/.ollama/models",
            ]
        for c in candidates:
            if not c:
                continue
            manifests = Path(c) / "manifests"
            try:
                if manifests.is_dir() and os.access(manifests, os.R_OK):
                    return Path(c)
            except OSError:
                continue
        return None

    @staticmethod
    def _manifest_model_name(rel_parts: tuple) -> Optional[str]:
        """Manifest path -> Ollama model name.

        manifests/<host>/<namespace>/<model>/<tag>, rendered the way Ollama
        itself does (Name.DisplayShortest): the default registry drops its
        host (`user/model:tag`) and its `library` namespace also drops the
        namespace (`model:tag`); other hosts keep the full prefix
        (`hf.co/user/model:tag`). Matching /api/tags exactly matters — the
        heartbeat's `loaded` flag and the catalogue's runtime_model_id both
        join on this string.

        Deliberate copy of backend/identity_resolver.manifest_model_name:
        the daemon ships to provider boxes without the backend package, so
        it cannot import it. Both are pinned by tests to the same renderings;
        change them together.
        """
        if len(rel_parts) != 4:
            return None
        host, namespace, model, tag = rel_parts
        if host == "registry.ollama.ai":
            if namespace == "library":
                return f"{model}:{tag}"
            return f"{namespace}/{model}:{tag}"
        return f"{host}/{namespace}/{model}:{tag}"

    def _scan_manifests(self, models_dir: Path) -> List[dict]:
        """Blocking walk of the manifests tree — call via to_thread."""
        items = []
        manifests_root = models_dir / "manifests"
        for path in sorted(manifests_root.rglob("*")):
            if not path.is_file():
                continue
            name = self._manifest_model_name(path.relative_to(manifests_root).parts)
            if name is None:
                continue
            try:
                with open(path) as f:
                    manifest = json.load(f)
            except (OSError, ValueError) as exc:
                logger.warning(f"Unreadable Ollama manifest {path}: {exc}")
                continue
            for layer in manifest.get("layers", []):
                # Only the model layer is the artifact; templates/params/
                # projector layers have their own mediaTypes.
                if not str(layer.get("mediaType", "")).endswith("image.model"):
                    continue
                digest = str(layer.get("digest", ""))
                # Split GGUFs carry several image.model layers; the first
                # is the artifact's identity (same convention as the
                # catalogue's digest = weights file), so size_bytes is
                # shard 1's size, not the whole artifact.
                items.append({
                    "local_name": name,
                    # The layer digest IS the GGUF file's sha256 (unlike
                    # /api/tags' digest, which hashes the manifest).
                    "sha256": digest.split(":", 1)[-1] if digest else None,
                    "size_bytes": layer.get("size"),
                })
                break
        return items

    # Negative-cache window for details lookups. Mirrors
    # VERSION_PROBE_RETRY_SECONDS: a failed /api/tags or /api/show is not
    # retried every heartbeat, or a black-holed Ollama endpoint would cost
    # 10s x (N+1) per beat, indefinitely, and the sweeper would mark this
    # worker offline for a runtime that is merely slow to answer metadata.
    DETAILS_RETRY_SECONDS = 60.0
    # Per-beat bound on /api/show lookups: N models converge over a few
    # beats instead of one beat costing N x timeout on a slow server.
    DETAILS_LOOKUPS_PER_BEAT = 4

    @staticmethod
    def _clean_quant(value) -> Optional[str]:
        """A quantization name, or None where Ollama has no answer.

        /api/tags answers the literal string "unknown" rather than omitting
        the field. Left as-is it reaches the catalogue as a quantization
        called "unknown" and lands in the entry id, so it is spelled as the
        absence it is.
        """
        if not value:
            return None
        text = str(value).strip()
        return None if text.lower() in ("", "unknown") else text

    async def _fetch_show_details(self, name: str) -> dict:
        """`context_length` and `quantization` from one /api/show call.

        /api/tags omits the quantization of anything pulled from a
        HuggingFace GGUF repo — `hf.co/<user>/<repo>-GGUF:<QUANT>` comes back
        as "unknown" while /api/show names it exactly. Both facts come from
        the same response because this call is made either way.
        """
        out = {"context_length": None, "quantization": None}
        try:
            response = await self._get_client().post(
                "/api/show", json={"model": name}, timeout=5.0,
            )
            response.raise_for_status()
            body = response.json()
            info = body.get("model_info") or {}
            for key, value in info.items():
                if key.endswith(".context_length"):
                    out["context_length"] = int(value)
                    break
            out["quantization"] = self._clean_quant(
                (body.get("details") or {}).get("quantization_level")
            )
        except Exception as exc:
            logger.debug(f"Could not fetch /api/show for {name}: {exc}")
        return out

    @staticmethod
    def _details_key(item: dict) -> Optional[str]:
        """Cache key = the ARTIFACT, never the name alone: sha256 when we have
        it; on the no-hash fallback path the name plus /api/tags' manifest
        digest, which changes when the model under that name is re-pulled.
        None when neither is known — such items are never cached."""
        if item.get("sha256"):
            return item["sha256"]
        if item.get("_tag_digest"):
            return f"{item['local_name']}@{item['_tag_digest']}"
        return None

    async def _attach_details(self, items: List[dict], tags: Optional[List[dict]] = None) -> List[dict]:
        """Add `details` so the backend can register a discovered model
        without a human filling in quant/params/ctx/family (#116 auto-adopt).

        Steady state costs zero API calls: every item is served from
        `_details_cache`. Lookups run only for uncached items, at most
        DETAILS_LOOKUPS_PER_BEAT per beat, and a failed lookup (API down,
        model unknown to the API) is remembered for DETAILS_RETRY_SECONDS.
        `tags` lets the no-hash path pass its already-fetched /api/tags.
        """
        now = time.monotonic()
        pending = []
        for item in items:
            key = self._details_key(item)
            item["_key"] = key
            if key in self._details_cache:
                item["details"] = self._details_cache[key]
            elif key in self._details_failed_at and now - self._details_failed_at[key] < self.DETAILS_RETRY_SECONDS:
                item["details"] = None
            else:
                item["details"] = None
                pending.append(item)

        api_recently_failed = (
            self._details_api_failed_at is not None
            and now - self._details_api_failed_at < self.DETAILS_RETRY_SECONDS
        )
        if pending and not api_recently_failed:
            if tags is None:
                tags = await self.list_models_detailed()
            if not tags:
                # API down or no models known to it: back off for the window.
                self._details_api_failed_at = now
            else:
                self._details_api_failed_at = None
                by_name = {m["name"]: m for m in tags}
                for item in pending[: self.DETAILS_LOOKUPS_PER_BEAT]:
                    tag = by_name.get(item["local_name"])
                    details = dict(tag["details"]) if tag else {}
                    shown = await self._fetch_show_details(item["local_name"])
                    details["context_length"] = shown["context_length"]
                    if details.get("quantization") is None:
                        details["quantization"] = shown["quantization"]
                    key = item["_key"]
                    if not any(v is not None for v in details.values()):
                        # Unknown to the API (on-disk/API desync): remember, retry later.
                        if key:
                            self._details_failed_at[key] = now
                        continue
                    item["details"] = details
                    if key:
                        if details["context_length"] is None:
                            # Partial (show failed): serve it now, retry the ctx later.
                            self._details_failed_at[key] = now
                        else:
                            self._details_cache[key] = details
                            self._details_failed_at.pop(key, None)

        for item in items:
            item.pop("_key", None)
            item.pop("_tag_digest", None)
        return items

    async def inventory(self) -> List[dict]:
        """Every model on disk with its GGUF file hash and descriptive details.

        A reading of the disk, not of the server: callers that advertise this
        upward must establish the server is reachable first, or they publish a
        catalogue nothing can serve (see main._collect_runtime_bundles).

        Reads Ollama's own manifests tree (the API never exposes per-file
        hashes) — pure filesystem, and once details are cached this path
        makes no API call at all. Falls back to /api/tags names with
        sha256=None when the tree is unreadable. Never raises.
        """
        try:
            models_dir = self._resolve_models_dir()
            items = []
            tags = None
            if models_dir is not None:
                items = await asyncio.to_thread(self._scan_manifests, models_dir)
            if not items:
                tags = await self.list_models_detailed()
                items = [
                    {"local_name": m["name"], "sha256": None, "size_bytes": None,
                     "_tag_digest": m.get("digest")}
                    for m in tags
                ]
            return self.tag_inventory(await self._attach_details(items, tags))
        except Exception as exc:
            logger.warning(f"Ollama inventory failed: {exc}")
            return []

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
            out = []
            for m in data.get("models", []):
                if not m.get("name"):
                    continue
                d = m.get("details") or {}
                out.append({
                    "name": m["name"],
                    "digest": m.get("digest"),   # MANIFEST digest — not artifact identity
                    "details": {
                        "quantization": self._clean_quant(d.get("quantization_level")),
                        "parameter_size": d.get("parameter_size"),
                        "family": d.get("family"),
                    },
                })
            return out
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
        "tool_choice":  ("Ollama does not support tool_choice — tool "
                         "selection is determined by the model from "
                         "the tools list"),
    }

    def capabilities(self) -> Dict[str, bool]:
        # Version-gated: an unknown version reads as "not supported", so a
        # server that never answered its health check is never offered a
        # logprobs batch. Completions rows are refused outright (execute()).
        supports_logprobs = (
            self.version is not None and parse_version(self.version) >= LOGPROBS_MIN_VERSION
        )
        return {"logprobs": supports_logprobs, "completions": False, "prompt_scoring": False}

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

        # ── thinking → think (Ollama ≥ 0.9) ──────────────────
        # Two spellings reach here and mean the same switch: Ollama's own
        # top-level `think`, and `chat_template_kwargs.enable_thinking`,
        # which is how vLLM/Qwen-style templates take it. Accepting both
        # lets one JSONL line drive either runtime.
        think = openai_body.get("think")
        if think is None:
            think = (openai_body.get("chat_template_kwargs") or {}).get("enable_thinking")
        if isinstance(think, bool):
            translated["think"] = think

        # ── logprobs / top_logprobs → top-level (Ollama ≥ 0.12.11) ─
        # Same names and meaning as OpenAI's. Below the minimum version the
        # server ignores them silently, so drop with a reason instead.
        if "logprobs" in openai_body or "top_logprobs" in openai_body:
            if self.capabilities()["logprobs"]:
                if openai_body.get("logprobs"):
                    translated["logprobs"] = True
                    top = openai_body.get("top_logprobs")
                    if isinstance(top, int) and top > 0:
                        translated["top_logprobs"] = top
            else:
                logger.warning(
                    "Parameters logprobs/top_logprobs dropped: Ollama %s < %s "
                    "does not return log-probabilities",
                    self.version or "unknown", ".".join(map(str, LOGPROBS_MIN_VERSION)),
                )

        return translated
        
    def _translate_response(self, ollama_response: dict) -> dict:
        """Ollama response -> OpenAI-compatible response format."""
        choices = []
        if "message" in ollama_response and ollama_response["message"]:
            message = dict(ollama_response["message"])
            # Ollama returns a reasoning model's trace as `thinking`; vLLM's
            # reasoning parser calls it `reasoning_content`. Expose both so a
            # reader of the output file has one field to look at.
            if message.get("thinking") and not message.get("reasoning_content"):
                message["reasoning_content"] = message["thinking"]
            choice = {
                "index": 0,
                "message": message,
                "finish_reason": "stop" if ollama_response.get("done") else "length",
            }
            # Ollama puts per-token log-probabilities at the top level of the
            # response; OpenAI puts them under the choice. The per-token keys
            # (token, logprob, bytes, top_logprobs) already match, so lift.
            if ollama_response.get("logprobs"):
                choice["logprobs"] = {"content": ollama_response["logprobs"]}
            choices.append(choice)
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
