"""
Catalogue mutations shared by the admin endpoint and the auto-adopt pass.

`adopt()` is the one way a worker-reported hash becomes a catalogue entry —
`POST /v1/models/adopt` (a human supplies the fields) and `auto_adopt_pass`
(the identity resolver supplies provenance, `details` supply the rest) both
call it, so the rules cannot drift: naming, one-hash-one-entry, the
runtime_model_id must be a name workers actually report.

Policy (environment):
  CATALOG_AUTO_ADOPT          off | registry-confirmed   (default registry-confirmed)
  CATALOG_AUTO_ADOPT_ENABLED  true | false               (default true)
      whether auto-adopted entries are user-selectable immediately, or
      staged (enabled=false) until an admin flips them in the Models tab.
"""
import logging
import os
import re
from dataclasses import dataclass
from typing import Optional

from catalog_seed import validate_entry_id
from identity_resolver import Confirmed, IdentityResolver, Unconfirmed
from models import (
    CatalogArtifactFile, ModelCatalog, Organization, RuntimeModel, ServingProfile,
    WorkerRuntime,
)
from reconciliation import UNREGISTERED, find_entry_by_hash, local_names_for_hash, reclassify_hash

logger = logging.getLogger(__name__)

AUTO_ADOPT_OFF = "off"
AUTO_ADOPT_REGISTRY = "registry-confirmed"

# Same starting-point heuristic as scripts/capture_catalog.py: weights plus
# an activation/KV margin. A verify-later value, never a promise.
_VRAM_FACTOR = 1.2
_VRAM_OVERHEAD_GB = 0.5

def _norm(s: str) -> str:
    """Family words with punctuation stripped: HF `model_type` and Ollama's
    `details.family` are the same words with different punctuation
    (`qwen2_5_vl` vs `qwen25vl`, `xlm-roberta` vs `xlmroberta`), so the
    sets below are normalized and so is whatever arrives."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


_EMBEDDING_FAMILIES = {_norm(f) for f in {"nomic-bert", "bert", "xlm-roberta", "bge"}}
_VISION_FAMILIES = {_norm(f) for f in {
    "gemma3", "gemma3n", "gemma4", "qwen2_vl", "qwen2_5_vl", "qwen3_vl",
    "llava", "mllama", "mistral3", "mistral3vision", "pixtral"}}


def auto_adopt_mode() -> str:
    mode = os.getenv("CATALOG_AUTO_ADOPT", AUTO_ADOPT_REGISTRY).strip().lower()
    return mode if mode in (AUTO_ADOPT_OFF, AUTO_ADOPT_REGISTRY) else AUTO_ADOPT_REGISTRY


def auto_adopt_enabled_default() -> bool:
    return os.getenv("CATALOG_AUTO_ADOPT_ENABLED", "true").strip().lower() not in ("0", "false", "no")


class AdoptError(Exception):
    """A rejected adoption; `status_code` maps straight onto HTTP."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def quant_slug(quantization) -> str:
    return re.sub(r"[^a-z0-9]", "", str(quantization or "").lower())


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def draft_slug(local_name: str, quantization=None) -> str:
    """Platform slug for a runtime name: `qwen3:4b` + Q4_K_M -> `qwen3-4b-q4km`,
    `hf.co/unsloth/x-GGUF:Q4_K_M` -> `hf-co-unsloth-x-gguf-q4km` (a tag that
    IS the quant is not repeated). Lowercase [a-z0-9-] only; quant slug
    suffix when known (the naming rules)."""
    repo, _, tag = local_name.partition(":")
    q = quant_slug(quantization)
    parts = [_slugify(repo)]
    if tag and not (q and quant_slug(tag) == q):
        parts.append(_slugify(tag))
    if q:
        parts.append(q)
    return "-".join(p for p in parts if p)


def unique_slug(db, slug: str, quantization=None) -> str:
    """`slug`, or `<stem>-2-<quant>`, `<stem>-3-<quant>`, ... until unused.
    The counter goes BEFORE the quant suffix so the naming rule ("ends with
    the quant slug") still holds for the disambiguated id."""
    q = quant_slug(quantization)
    stem = slug[: -len(q) - 1] if q and slug.endswith("-" + q) else slug
    candidate, n = slug, 1
    while db.query(ModelCatalog.id).filter(ModelCatalog.id == candidate).first() is not None:
        n += 1
        candidate = f"{stem}-{n}-{q}" if q else f"{stem}-{n}"
    return candidate


def estimate_vram_gb(size_bytes) -> Optional[float]:
    if not size_bytes:
        return None
    return round(size_bytes / (1024 ** 3) * _VRAM_FACTOR + _VRAM_OVERHEAD_GB, 1)


def infer_task_and_capabilities(local_name: str, details: Optional[dict]) -> tuple:
    """(task_type, capabilities) from the runtime's family and the name."""
    family = _norm((details or {}).get("family"))
    name = local_name.lower()
    if family in _EMBEDDING_FAMILIES or "embed" in name:
        return "embedding", {"embeddings": True, "json_mode": False, "vision": False}
    vision = family in _VISION_FAMILIES or "vl" in name.split(":")[0].split("-") or "llava" in name
    return "chat", {"json_mode": True, "vision": bool(vision), "embeddings": False}


def adopt(
    db,
    *,
    sha256: str,
    entry_id: str,
    display_name: str,
    runtime: str,
    runtime_model_id: str,
    vram_gb: Optional[float],
    extra_names: Optional[list] = None,
    quantization: Optional[str] = None,
    task_type: str = "chat",
    capabilities: Optional[dict] = None,
    lineage: Optional[str] = None,
    size_gb: Optional[float] = None,
    parameter_size: Optional[str] = None,
    context_length: Optional[int] = None,
    org_id: Optional[str] = None,
    source_type: str = "worker-adopted",
    source_ref: Optional[str] = None,
    source_revision: Optional[str] = None,
    homepage_url: Optional[str] = None,
    adopted_by: Optional[str] = None,
    enabled: bool = True,
) -> tuple:
    """Create a catalogue entry (status `unverified`) + one serving profile
    for a worker-reported hash and re-classify every row carrying it.
    Returns (entry, rows_verified). Raises AdoptError on any rule breach.
    Does not commit."""
    errors = validate_entry_id(entry_id, {"quantization": quantization})
    if errors:
        raise AdoptError(400, "; ".join(errors))
    if db.query(ModelCatalog).filter(ModelCatalog.id == entry_id).first() is not None:
        raise AdoptError(409, f"catalogue id {entry_id!r} already exists")
    existing = find_entry_by_hash(db, sha256)
    if existing is not None:
        raise AdoptError(
            409,
            f"sha256 already pinned by catalogue entry {existing.id!r} "
            f"(status={existing.status}, enabled={existing.enabled}) — "
            "enable/activate that entry instead of adopting again",
        )
    # The picker matches worker rows on the entry's runtime ids, so an id no
    # worker reports for this hash would flip rows to available yet never
    # dispatch — a false success.
    names = local_names_for_hash(db, sha256)
    for rmid in [runtime_model_id] + [n for n in (extra_names or []) if n != runtime_model_id]:
        if names and rmid not in names:
            raise AdoptError(
                400,
                f"runtime_model_id {rmid!r} is not what any worker "
                f"reports for this hash; workers call it {sorted(names)}",
            )
    if org_id is not None and db.get(Organization, org_id) is None:
        raise AdoptError(400, f"unknown org_id {org_id!r}")

    digest = sha256.strip().lower().split(":", 1)[-1]
    entry = ModelCatalog(
        id=entry_id,
        display_name=display_name,
        runtime=runtime,                       # legacy pair kept dual-written
        runtime_model_id=runtime_model_id,
        digest=digest,
        quantization=quantization,
        parameter_size=parameter_size,
        context_length=context_length,
        vram_gb=vram_gb,
        size_gb=size_gb,
        task_type=task_type,
        capabilities=capabilities,
        lineage=lineage,
        source_type=source_type,
        source_ref=source_ref,
        source_revision=source_revision,
        homepage_url=homepage_url,
        org_id=org_id,
        status="unverified",
        enabled=enabled,
        adopted_by=adopted_by,
    )
    db.add(entry)
    db.flush()
    extras = [n for n in (extra_names or []) if n != runtime_model_id] or None
    db.add(ServingProfile(catalog_id=entry.id, runtime=runtime,
                          runtime_model_id=runtime_model_id, runtime_model_ids=extras))
    db.flush()
    verified = reclassify_hash(db, digest)
    return entry, verified


# ─── Auto-adopt pass ─────────────────────────────────────────

@dataclass
class _Candidate:
    digest: str
    names: list          # local names workers report, most common first
    runtime: str
    details: Optional[dict]
    size_bytes: Optional[int]
    files: Optional[list] = None   # [{file, sha256, size_bytes}] for multi-shard artifacts


def _candidates(db) -> list:
    """One candidate per quarantined hash, across every worker/runtime."""
    rows = (
        db.query(RuntimeModel, WorkerRuntime.engine)
        .join(WorkerRuntime, RuntimeModel.runtime_id == WorkerRuntime.id)
        .filter(RuntimeModel.status == UNREGISTERED, RuntimeModel.digest.isnot(None))
        .all()
    )
    by_digest: dict = {}
    for row, engine in rows:
        d = row.digest.lower().split(":", 1)[-1]
        c = by_digest.setdefault(d, _Candidate(d, [], engine, None, None))
        if row.name not in c.names:
            c.names.append(row.name)
        if c.details is None and row.details:
            c.details = row.details
        if c.size_bytes is None and row.size_bytes:
            c.size_bytes = row.size_bytes
        if c.files is None and row.files:
            c.files = row.files
    return list(by_digest.values())


def _pick_served_alias(names, source_ref):
    """The primary served name to pin for a vLLM artifact, or None.

    A short reported alias (no '/', so never a filesystem path or a repo id),
    chosen deterministically (shortest, then lexicographic) — `aliases[0]`
    used to read an unordered DB query, so which box's alias got pinned was
    a coin flip and the other boxes' aliases never dispatched. When no alias
    is reported, the repo id (the model is served under it). Paths are never
    valid body.model values and lose by construction."""
    names = set(names or [])
    short = sorted(n for n in names if n and "/" not in n)
    if short:
        return min(short, key=lambda n: (len(n), n))
    if source_ref and source_ref in names:
        return source_ref
    return None


def auto_adopt_pass(db, resolver: IdentityResolver, *, enabled: Optional[bool] = None) -> int:
    """Adopt every quarantined hash the resolver confirms. Returns the number
    of entries created. Commits per adoption so one failure cannot roll back
    the rest. Unconfirmed hashes stay quarantined for the human path."""
    if enabled is None:
        enabled = auto_adopt_enabled_default()
    adopted = 0
    for cand in _candidates(db):
        details = cand.details or {}
        # vLLM daemons know where the bytes came from (HF hub cache): repo,
        # commit and every shard hash travel as hints; the resolver then
        # confirms ALL shards at that commit instead of parsing a served
        # alias that has no public meaning.
        hints = {
            "source_ref": details.get("source_ref"),
            "source_revision": details.get("source_revision"),
            "files": [f.get("sha256") for f in (cand.files or []) if f.get("sha256")] or None,
        }
        confirmed = None
        for name in cand.names:
            result = resolver.resolve(name, cand.digest, **hints)
            if (isinstance(result, Unconfirmed) and result.reason == "digest-mismatch"
                    and hints.get("source_revision")):
                # The hinted commit no longer holds the shards (a tag was
                # re-published, or the box's cache predates it). Not a
                # verdict: retry at the repo's current commit first.
                result = resolver.resolve(name, cand.digest,
                                          source_ref=hints.get("source_ref"),
                                          files=hints.get("files"))
            if isinstance(result, Confirmed):
                confirmed = (name, result)
                break
            logger.debug("auto-adopt: %s@%s not confirmed (%s)", name, cand.digest[:12], result.reason)
        if confirmed is None:
            continue
        name, res = confirmed
        served = name
        extra_names = None
        if cand.runtime == "vllm":
            # The serving profile must pin the name dispatch sends as
            # body.model. Pin a deterministic served alias (never a path,
            # never DB order), and make every other reported non-path name an
            # extra profile name — boxes serving the same artifact under
            # different --served-model-name stay schedulable on one entry.
            served = _pick_served_alias(cand.names, res.source_ref)
            if served is None:
                logger.info(
                    "auto-adopt: no unambiguous served name for %s (names=%s) — "
                    "leaving quarantined for a human",
                    cand.digest[:12], sorted(set(cand.names)),
                )
                continue
            extra_names = [n for n in sorted(set(cand.names))
                           if n != served and not n.startswith(("/", "./", "~"))]
        quant = details.get("quantization")
        task_type, capabilities = infer_task_and_capabilities(served, details)
        size_gb = round(cand.size_bytes / (1024 ** 3), 2) if cand.size_bytes else None
        # Slug from the public identity when we have it (Org/Repo), else the
        # runtime name — `served-alias-bf16` would name a deployment, not a model.
        slug_base = res.source_ref if (cand.runtime == "vllm" and res.source_type == "huggingface") else served
        try:
            entry, verified = adopt(
                db,
                sha256=cand.digest,
                entry_id=unique_slug(db, draft_slug(slug_base, quant), quant),
                display_name=served,
                runtime=cand.runtime,
                runtime_model_id=served,
                extra_names=extra_names,
                vram_gb=estimate_vram_gb(cand.size_bytes),
                quantization=quant,
                task_type=task_type,
                capabilities=capabilities,
                size_gb=size_gb,
                parameter_size=details.get("parameter_size"),
                context_length=details.get("context_length"),
                source_type=res.source_type,
                source_ref=res.source_ref,
                source_revision=res.source_revision,
                homepage_url=res.homepage_url,
                adopted_by="auto",
                enabled=enabled,
            )
            if cand.files:
                # Multi-shard artifact (vLLM safetensors): pin every shard so
                # "artifact present" means complete, not just shard 1.
                for i, f in enumerate(cand.files):
                    db.add(CatalogArtifactFile(
                        catalog_id=entry.id, file=f["file"],
                        role="weights" if i == 0 else "shard",
                        sha256=f.get("sha256"), size_bytes=f.get("size_bytes"),
                    ))
            elif res.source_file:
                # HF confirmation is file-agnostic (the hash is the identity,
                # the :tag may lie) — pin the file that actually matched so the
                # pull reference is complete: repo + revision + this path.
                db.add(CatalogArtifactFile(
                    catalog_id=entry.id, file=res.source_file, role="weights",
                    sha256=cand.digest, size_bytes=cand.size_bytes,
                ))
            db.commit()
        except AdoptError as exc:
            db.rollback()
            logger.warning("auto-adopt: %s@%s skipped: %s", name, cand.digest[:12], exc.detail)
            continue
        adopted += 1
        logger.info(
            "auto-adopt: %s -> catalogue %r (%s %s, vram~%s GB, enabled=%s, %d worker rows verified)",
            name, entry.id, res.source_type, res.source_ref, entry.vram_gb, enabled, verified,
        )
    return adopted


_resolver: Optional[IdentityResolver] = None


def run_auto_adopt_once() -> int:
    """Sweeper entry point: own session, process-wide resolver (its caches
    live for the process). Blocking — call via asyncio.to_thread."""
    global _resolver
    if auto_adopt_mode() == AUTO_ADOPT_OFF:
        return 0
    from database import SessionLocal
    if _resolver is None:
        _resolver = IdentityResolver()
    db = SessionLocal()
    try:
        return auto_adopt_pass(db, _resolver)
    finally:
        db.close()
