"""Identity resolver: confirm a quarantined artifact hash against its public
source with no human in the loop (#116 follow-up, step 2 of auto-adopt).

A worker reports `(local_name, sha256)` for every artifact on disk. When the
hash matches no catalogue entry the row is quarantined as `unregistered`.
This module answers one question about such a row: **is this exact file the
one the public registry serves under that name?**

    Ollama names  `gemma3:1b`, `user/model:tag`
        -> GET https://registry.ollama.ai/v2/{ns}/{model}/manifests/{tag}
        -> compare the `application/vnd.ollama.image.model` layer digest
           (which IS the GGUF file's sha256) to the reported hash.
    HF names      `hf.co/{user}/{repo}[:tag]`, `huggingface.co/...`
        -> GET https://huggingface.co/api/models/{user}/{repo}      (revision)
        -> GET .../tree/{revision}?recursive=true
        -> compare every LFS file's `lfs.oid` (its sha256) to the hash.

Anything else — an unsupported host, a mismatch, a 404, a network error —
is `Unconfirmed` and the row stays quarantined. Confirmation is by hash
equality only; the name merely says where to look.

Caching: a `Confirmed` answer is final (the bytes cannot stop being the
registry's bytes) and is kept for the resolver's lifetime. An `Unconfirmed`
answer is kept for `negative_ttl` seconds so a flaky registry, or a model
that is simply not public, is not re-fetched on every sweep.

Runs OFF the request path: the auto-adopt pass calls `resolve()`; the
heartbeat never does. The client is synchronous (`httpx.Client`) so
`scripts/capture_catalog.py` can share it; an asyncio caller wraps
`resolve()` in `asyncio.to_thread`. No backend-internal imports on purpose
— the script imports this module from outside the backend package.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional, Tuple, Union

import httpx

DEFAULT_REGISTRY = "registry.ollama.ai"
HF_HOSTS = ("hf.co", "huggingface.co")
MODEL_MEDIA_SUFFIX = "image.model"
MANIFEST_ACCEPT = "application/vnd.docker.distribution.manifest.v2+json"
DEFAULT_TIMEOUT = 20.0
DEFAULT_NEGATIVE_TTL = 15 * 60  # seconds — definitive answers (mismatch, 404)
# Transient failures (rate limit, 5xx, network) are remembered only briefly:
# a 429 blip must not quarantine a confirmable hash for 15 minutes.
DEFAULT_TRANSIENT_TTL = 60.0
_TRANSIENT_REASON_PREFIXES = ("network-error", "http-429", "http-5", "bad-json")


def is_transient(reason: str) -> bool:
    return str(reason).startswith(_TRANSIENT_REASON_PREFIXES)

SOURCE_OLLAMA = "ollama-library"   # matches the catalogue's `source_type` vocabulary
SOURCE_HF = "huggingface"


@dataclass(frozen=True)
class Confirmed:
    """The reported bytes are what the public source serves under the name.

    `source_ref` + `source_revision` is the pull reference the catalogue
    stores (HF repo + commit; Ollama library path with `source_revision`
    NULL, matching seeded entries). `digest` is the bare sha256 that was
    confirmed — the identity key the adopting entry pins.

    `source_file` is the path inside the repo whose bytes matched (HF only;
    Ollama's model layer has no path). Confirmation is deliberately
    file-agnostic — the hash is the identity, the `:tag` in a local name is
    a human label that may be wrong (mispull, upstream rename) — so the
    matched path is recorded here to make the pull reference complete
    without re-listing the tree, and to let the adopting entry pin the
    exact file in `catalog_artifact_files`.
    """
    source_type: str
    source_ref: str
    source_revision: Optional[str]
    digest: str
    homepage_url: Optional[str] = None
    source_file: Optional[str] = None


@dataclass(frozen=True)
class Unconfirmed:
    """Not proven public. `reason` is a short machine-readable token
    (`no-hash`, `unsupported-registry`, `digest-mismatch`, `http-404`,
    `network-error`, ...) — for logs and the Models tab, never for branching
    adoption logic: every Unconfirmed stays quarantined."""
    reason: str


Resolution = Union[Confirmed, Unconfirmed]


# ─── Name and digest helpers (shared with scripts/capture_catalog.py) ────────

def bare_digest(d) -> Optional[str]:
    """Canonical form: bare lowercase hex, no `sha256:` prefix."""
    if not d:
        return None
    d = str(d).strip().lower()
    return d.split(":", 1)[1] if ":" in d else d


def manifest_model_name(host: str, namespace: str, model: str, tag: str) -> str:
    """Ollama's rendered name for a manifest path (mirrors DisplayShortest):
    the default registry drops its host; its `library` namespace also drops
    the namespace. Other hosts keep the full prefix (hf.co/user/model:tag)."""
    if host == DEFAULT_REGISTRY:
        return f"{model}:{tag}" if namespace == "library" else f"{namespace}/{model}:{tag}"
    return f"{host}/{namespace}/{model}:{tag}"


def model_layer_digests(manifest: dict) -> list:
    """Bare sha256 of every `...image.model` layer in an Ollama manifest."""
    out = []
    for layer in manifest.get("layers", []) or []:
        if str(layer.get("mediaType", "")).endswith(MODEL_MEDIA_SUFFIX):
            d = bare_digest(layer.get("digest"))
            if d:
                out.append(d)
    return out


def model_layer_digest(manifest: dict) -> Optional[str]:
    """First model-layer digest, or None (a manifest with no weights)."""
    digests = model_layer_digests(manifest)
    return digests[0] if digests else None


@dataclass(frozen=True)
class OllamaName:
    namespace: str
    model: str
    tag: str

    @property
    def library_path(self) -> str:
        return self.model if self.namespace == "library" else f"{self.namespace}/{self.model}"

    @property
    def manifest_url(self) -> str:
        return f"https://{DEFAULT_REGISTRY}/v2/{self.namespace}/{self.model}/manifests/{self.tag}"

    @property
    def homepage_url(self) -> str:
        return f"https://ollama.com/library/{self.model}" if self.namespace == "library" \
            else f"https://ollama.com/{self.namespace}/{self.model}"


@dataclass(frozen=True)
class HfName:
    user: str
    repo: str
    tag: Optional[str]

    @property
    def repo_id(self) -> str:
        return f"{self.user}/{self.repo}"

    @property
    def api_url(self) -> str:
        return f"https://huggingface.co/api/models/{self.repo_id}"

    @property
    def homepage_url(self) -> str:
        return f"https://huggingface.co/{self.repo_id}"


def _hf_from_repo_id(repo_id: str) -> Optional[HfName]:
    """`Org/Repo` (a daemon-supplied hint, not a runtime name) -> HfName."""
    parts = [p for p in str(repo_id or "").strip().split("/") if p]
    if len(parts) != 2:
        return None
    return HfName(user=parts[0], repo=parts[1], tag=None)


def parse_local_name(local_name: str) -> Union[OllamaName, HfName, None]:
    """Classify a runtime model name by where its manifest lives.

    `gemma3:1b` / `gemma3` -> library; `user/model:tag` -> community;
    `registry.ollama.ai/...` -> its own host stripped; `hf.co/user/repo:tag`
    -> HF. Any other host (`some.host/ns/model`) -> None: only the local
    manifests tree can hash those, no public source to ask.
    """
    if not local_name or not isinstance(local_name, str):
        return None
    name, _, tag = local_name.strip().partition(":")
    parts = [p for p in name.split("/") if p]
    if not parts:
        return None
    if parts[0].lower() in HF_HOSTS:
        if len(parts) != 3:
            return None
        return HfName(user=parts[1], repo=parts[2], tag=tag or None)
    if parts[0] == DEFAULT_REGISTRY:
        parts = parts[1:]
    if len(parts) > 2 or (len(parts) == 2 and "." in parts[0]):
        return None
    if len(parts) == 2:
        namespace, model = parts
    else:
        namespace, model = "library", parts[0]
    if not model or not namespace:
        return None
    return OllamaName(namespace=namespace, model=model, tag=tag or "latest")


def _default_client() -> httpx.Client:
    return httpx.Client(
        timeout=httpx.Timeout(DEFAULT_TIMEOUT),
        follow_redirects=True,
        headers={"User-Agent": "sheshnag-identity-resolver"},
    )


def registry_file_digest(
    runtime_model_id: str, client: Optional[httpx.Client] = None
) -> Tuple[Optional[str], Optional[str]]:
    """File sha256 for a default-registry model via its manifest API — no
    blob download. Returns `(digest, None)` or `(None, reason)`.

    This is the lookup `scripts/capture_catalog.py` uses to pin manifest
    entries; `IdentityResolver` uses the same request to *confirm* one.
    """
    parsed = parse_local_name(runtime_model_id)
    if not isinstance(parsed, OllamaName):
        return None, "non-default registry name, no registry fallback"
    own = client is None
    client = client or _default_client()
    try:
        r = client.get(parsed.manifest_url, headers={"Accept": MANIFEST_ACCEPT})
        r.raise_for_status()
        digest = model_layer_digest(r.json())
        return (digest, None) if digest else (None, "manifest has no model layer")
    except Exception as exc:  # network, HTTP status, bad JSON — all "unavailable"
        return None, f"registry manifest unavailable: {exc}"
    finally:
        if own:
            client.close()


# ─── Resolver ────────────────────────────────────────────────────────────────

class IdentityResolver:
    """Confirms `(local_name, sha256)` pairs against their public source.

    One instance per process is intended: the caches live on it. `client`
    is injectable (tests pass an `httpx.MockTransport`-backed client);
    `clock` is the monotonic seconds source the negative TTL is measured
    with.
    """

    def __init__(
        self,
        client: Optional[httpx.Client] = None,
        negative_ttl: float = DEFAULT_NEGATIVE_TTL,
        clock: Callable[[], float] = time.monotonic,
        transient_ttl: float = DEFAULT_TRANSIENT_TTL,
    ):
        # Only a client this resolver created is closed by close(); an
        # injected one belongs to the caller (mirrors registry_file_digest).
        self._owns_client = client is None
        self._client = client or _default_client()
        self._negative_ttl = negative_ttl
        self._transient_ttl = transient_ttl
        self._clock = clock
        self._lock = threading.Lock()
        self._confirmed: dict = {}    # (name, sha256) -> Confirmed        (final)
        self._unconfirmed: dict = {}  # (name, sha256) -> (Unconfirmed, expires_at)

    # -- public ---------------------------------------------------------------

    def resolve(
        self,
        local_name: str,
        sha256: Optional[str],
        *,
        source_ref: Optional[str] = None,
        source_revision: Optional[str] = None,
        files: Optional[list] = None,
    ) -> Resolution:
        """Confirm `(local_name, sha256)`.

        The optional hints come from a daemon that already knows where the
        bytes came from (vLLM's HF hub cache): `source_ref` is the HF repo
        id — used instead of parsing `local_name`, which for vLLM is a
        served alias or a bare `Org/Repo` that would otherwise look like an
        Ollama community name — `source_revision` the commit to check at,
        and `files` every shard's sha256. With `files`, confirmation
        requires EVERY shard to be present in the repo at that revision,
        not just the identity shard.
        """
        digest = bare_digest(sha256)
        if not digest:
            return Unconfirmed("no-hash")
        digests = tuple(sorted({bare_digest(f) for f in (files or []) if bare_digest(f)} | {digest}))
        # Key on the SOURCE, not the local name: the auto-adopt loop asks
        # about one hash under several served names (aliases, repo id), and
        # every one of them resolves the same upstream repo — without this
        # each name burns its own request budget per negative TTL.
        key = (source_ref or local_name, digests, source_ref, source_revision)

        cached = self._cached(key)
        if cached is not None:
            return cached

        if source_ref:
            parsed = _hf_from_repo_id(source_ref)
            if parsed is None:
                result = Unconfirmed("unsupported-registry")
            else:
                result = self._resolve_hf(parsed, digest, digests=digests, revision=source_revision)
        else:
            parsed = parse_local_name(local_name)
            if isinstance(parsed, OllamaName):
                result = self._resolve_ollama(parsed, digest)
            elif isinstance(parsed, HfName):
                result = self._resolve_hf(parsed, digest, digests=digests)
            else:
                result = Unconfirmed("unsupported-registry")

        self._remember(key, result)
        return result

    def close(self) -> None:
        """Release the HTTP client if this resolver created it. An injected
        client is left open — the caller owns its lifetime."""
        if self._owns_client:
            self._client.close()

    # -- caching --------------------------------------------------------------

    def _cached(self, key) -> Optional[Resolution]:
        with self._lock:
            if key in self._confirmed:
                return self._confirmed[key]
            entry = self._unconfirmed.get(key)
            if entry is None:
                return None
            result, expires_at = entry
            if self._clock() < expires_at:
                return result
            del self._unconfirmed[key]
            return None

    def _remember(self, key, result: Resolution) -> None:
        with self._lock:
            if isinstance(result, Confirmed):
                self._confirmed[key] = result
                self._unconfirmed.pop(key, None)
            else:
                ttl = self._transient_ttl if is_transient(result.reason) else self._negative_ttl
                self._unconfirmed[key] = (result, self._clock() + ttl)

    # -- sources --------------------------------------------------------------

    def _get_json(self, url: str, **kwargs):
        """(payload, links, None) or (None, None, Unconfirmed), where links
        is the parsed `Link` header (pagination). HTTP status and transport
        failures are both Unconfirmed — with distinguishable reasons."""
        try:
            r = self._client.get(url, **kwargs)
        except httpx.HTTPError as exc:
            return None, None, Unconfirmed(f"network-error: {type(exc).__name__}")
        if r.status_code != 200:
            return None, None, Unconfirmed(f"http-{r.status_code}")
        try:
            return r.json(), dict(r.links), None
        except ValueError:
            return None, None, Unconfirmed("bad-json")

    def _resolve_ollama(self, name: OllamaName, digest: str) -> Resolution:
        manifest, _links, err = self._get_json(name.manifest_url, headers={"Accept": MANIFEST_ACCEPT})
        if err:
            return err
        if not isinstance(manifest, dict):
            return Unconfirmed("bad-json")
        served = model_layer_digests(manifest)
        if not served:
            return Unconfirmed("no-model-layer")
        if digest not in served:
            return Unconfirmed("digest-mismatch")
        return Confirmed(
            source_type=SOURCE_OLLAMA,
            source_ref=name.library_path,
            source_revision=None,
            digest=digest,
            homepage_url=name.homepage_url,
        )

    def _resolve_hf(
        self, name: HfName, digest: str, digests: Optional[tuple] = None,
        revision: Optional[str] = None,
    ) -> Resolution:
        """Confirm at `revision` when the caller knows it (the cached commit
        vLLM serves), else at the repo's current commit. Every hash in
        `digests` (default: just `digest`) must be an LFS file in the tree.
        """
        if not revision:
            info, _links, err = self._get_json(name.api_url)
            if err:
                return err
            revision = (info or {}).get("sha") if isinstance(info, dict) else None
        # The tree endpoint is paginated (1000 entries/page): a repo whose
        # non-weight files fill the early pages digest-mismatches forever
        # unless we follow the Link header. Stop early once every required
        # shard has been seen.
        required = set(digests or (digest,))
        paths_by_oid = {}
        url, params = f"{name.api_url}/tree/{revision or 'main'}", {"recursive": "true"}
        for _page in range(100):    # 100k files: beyond any model repo
            tree, links, err = self._get_json(url, params=params)
            if err:
                return err
            if not isinstance(tree, list):
                return Unconfirmed("bad-json")
            for entry in tree:
                if not isinstance(entry, dict):
                    continue
                if entry.get("path") == "adapter_config.json":
                    return Unconfirmed("lora-adapter")
                lfs = entry.get("lfs")
                oid = bare_digest(lfs.get("oid")) if lfs else None
                if oid:
                    paths_by_oid.setdefault(oid, entry.get("path"))
            if required.issubset(paths_by_oid):
                break
            nxt = (links.get("next") or {}).get("url", "")
            if not nxt.startswith("http"):
                break
            # The next URL carries its own query (the cursor): passing
            # params here would make httpx rebuild it from scratch.
            url, params = nxt, None
        if not required.issubset(paths_by_oid):
            return Unconfirmed("digest-mismatch")
        return Confirmed(
            source_type=SOURCE_HF,
            source_ref=name.repo_id,
            source_revision=revision,
            digest=digest,
            homepage_url=name.homepage_url,
            source_file=paths_by_oid.get(digest),
        )
