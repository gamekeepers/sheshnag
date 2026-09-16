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
DEFAULT_NEGATIVE_TTL = 15 * 60  # seconds

SOURCE_OLLAMA = "ollama-library"   # matches the catalogue's `source_type` vocabulary
SOURCE_HF = "huggingface"


@dataclass(frozen=True)
class Confirmed:
    """The reported bytes are what the public source serves under the name.

    `source_ref` + `source_revision` is the pull reference the catalogue
    stores (HF repo + commit; Ollama library path with `source_revision`
    NULL, matching seeded entries). `digest` is the bare sha256 that was
    confirmed — the identity key the adopting entry pins.
    """
    source_type: str
    source_ref: str
    source_revision: Optional[str]
    digest: str
    homepage_url: Optional[str] = None


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
    ):
        self._client = client or _default_client()
        self._negative_ttl = negative_ttl
        self._clock = clock
        self._lock = threading.Lock()
        self._confirmed: dict = {}    # (name, sha256) -> Confirmed        (final)
        self._unconfirmed: dict = {}  # (name, sha256) -> (Unconfirmed, expires_at)

    # -- public ---------------------------------------------------------------

    def resolve(self, local_name: str, sha256: Optional[str]) -> Resolution:
        digest = bare_digest(sha256)
        if not digest:
            return Unconfirmed("no-hash")
        key = (local_name, digest)

        cached = self._cached(key)
        if cached is not None:
            return cached

        parsed = parse_local_name(local_name)
        if isinstance(parsed, OllamaName):
            result = self._resolve_ollama(parsed, digest)
        elif isinstance(parsed, HfName):
            result = self._resolve_hf(parsed, digest)
        else:
            result = Unconfirmed("unsupported-registry")

        self._remember(key, result)
        return result

    def close(self) -> None:
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
                self._unconfirmed[key] = (result, self._clock() + self._negative_ttl)

    # -- sources --------------------------------------------------------------

    def _get_json(self, url: str, **kwargs):
        """(payload, None) or (None, Unconfirmed). HTTP status and transport
        failures are both Unconfirmed — with distinguishable reasons."""
        try:
            r = self._client.get(url, **kwargs)
        except httpx.HTTPError as exc:
            return None, Unconfirmed(f"network-error: {type(exc).__name__}")
        if r.status_code != 200:
            return None, Unconfirmed(f"http-{r.status_code}")
        try:
            return r.json(), None
        except ValueError:
            return None, Unconfirmed("bad-json")

    def _resolve_ollama(self, name: OllamaName, digest: str) -> Resolution:
        manifest, err = self._get_json(name.manifest_url, headers={"Accept": MANIFEST_ACCEPT})
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

    def _resolve_hf(self, name: HfName, digest: str) -> Resolution:
        info, err = self._get_json(name.api_url)
        if err:
            return err
        revision = (info or {}).get("sha") if isinstance(info, dict) else None
        tree, err = self._get_json(
            f"{name.api_url}/tree/{revision or 'main'}", params={"recursive": "true"}
        )
        if err:
            return err
        if not isinstance(tree, list):
            return Unconfirmed("bad-json")
        for entry in tree:
            lfs = entry.get("lfs") if isinstance(entry, dict) else None
            if lfs and bare_digest(lfs.get("oid")) == digest:
                return Confirmed(
                    source_type=SOURCE_HF,
                    source_ref=name.repo_id,
                    source_revision=revision,
                    digest=digest,
                    homepage_url=name.homepage_url,
                )
        return Unconfirmed("digest-mismatch")
