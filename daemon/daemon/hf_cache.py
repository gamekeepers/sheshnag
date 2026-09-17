"""
Identity for vLLM models from the Hugging Face hub cache.

vLLM serves HF models out of `$HF_HUB_CACHE` (default
`~/.cache/huggingface/hub`), and that cache is content-addressed the same
way Ollama's store is: every LFS file lives once under
`models--{Org}--{Name}/blobs/<sha256>` and each `snapshots/<commit>/` holds
symlinks into it. So the artifact identity (shard hashes) and the pull
reference (repo + commit) are both on disk — no API exposes them.

Pure filesystem helpers, blocking; the executor calls them via to_thread.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Optional

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_REPO_ID = re.compile(r"^[A-Za-z0-9][\w.\-]*/[A-Za-z0-9][\w.\-]*$")
_DTYPE_SLUG = {"bfloat16": "bf16", "float16": "fp16", "float32": "fp32", "float8_e4m3fn": "fp8"}
_BYTES_PER_PARAM = {"bfloat16": 2, "float16": 2, "float32": 4}


def resolve_hub_cache(explicit: Optional[str] = None) -> Optional[Path]:
    """The hub cache dir: explicit config, $HF_HUB_CACHE, $HF_HOME/hub,
    then the default. None when none exists (no cached models here)."""
    candidates = [
        explicit,
        os.environ.get("HF_HUB_CACHE"),
        os.path.join(os.environ["HF_HOME"], "hub") if os.environ.get("HF_HOME") else None,
        os.path.expanduser("~/.cache/huggingface/hub"),
    ]
    for c in candidates:
        if c and Path(c).is_dir():
            return Path(c)
    return None


def repo_dir_name(repo_id: str) -> str:
    return "models--" + repo_id.replace("/", "--")


def repo_id_from_dir_name(name: str) -> Optional[str]:
    if not name.startswith("models--"):
        return None
    parts = name[len("models--"):].split("--")
    return "/".join(parts) if len(parts) >= 2 else None


def snapshot_for(repo_dir: Path) -> Optional[tuple]:
    """(revision, snapshot_path) for a cached repo, or None when the choice
    is ambiguous. A single snapshot dir is unambiguous no matter what the
    refs say — vLLM can only serve what is in its cache, and the dir name IS
    the commit SHA. With several (e.g. `--revision v0.5.0` and a later
    `main` pull), only `refs/main` picking an existing snapshot may be
    trusted: tags live under refs/tags/<tag>, commit pulls write no ref,
    and mtime order is a guess, not an identity.
    """
    snaps = repo_dir / "snapshots"
    if not snaps.is_dir():
        return None
    dirs = [d for d in snaps.iterdir() if d.is_dir()]
    if not dirs:
        return None
    if len(dirs) == 1:
        return dirs[0].name, dirs[0]
    ref = repo_dir / "refs" / "main"
    try:
        rev = ref.read_text().strip()
        if rev and (snaps / rev).is_dir():
            return rev, snaps / rev
    except OSError:
        pass
    return None


def locate(cache: Optional[Path], root: str) -> Optional[tuple]:
    """(repo_id, revision, snapshot_path) for what vLLM reports as `root`.

    `root` is either an HF repo id (`Org/Name` — the usual `vllm serve`
    argument) or a filesystem path. The PATH is checked first: a real
    directory is a local checkout and never gets a public identity — a
    relative `Org/Name` dir in the CWD must not be read as the PUBLIC
    `Org/Name` cache snapshot, which serves different bytes. Only a
    directory that IS a cache snapshot (`snapshots/<rev>/`) maps back to
    its repo. Only a non-directory string may then be tried as a cached
    repo id; anything else is a local model with no public identity ->
    None.
    """
    if not root:
        return None
    p = Path(root)
    if p.is_dir():                                   # local checkout: no public identity
        # ...unless it IS a cache snapshot: .../models--Org--Name/snapshots/<rev>[/...]
        for parent in [p] + list(p.parents):
            if parent.parent.name == "snapshots":
                repo_id = repo_id_from_dir_name(parent.parent.parent.name)
                if repo_id:
                    return repo_id, parent.name, parent
        return None
    if _REPO_ID.match(root) and cache is not None:   # only then: a cached repo id
        found = snapshot_for(cache / repo_dir_name(root))
        return (root, found[0], found[1]) if found else None
    return None


def _blob_sha256(path: Path) -> Optional[str]:
    """The LFS blob a snapshot symlink points at is named by its sha256."""
    try:
        if path.is_symlink():
            name = Path(os.readlink(path)).name
            return name if _HEX64.match(name) else None
    except OSError:
        return None
    return None


def _quantization(config: dict) -> Optional[str]:
    qc = config.get("quantization_config")
    if isinstance(qc, dict) and qc:
        method = str(qc.get("quant_method") or qc.get("method") or "quant").lower()
        bits = qc.get("bits") or qc.get("weight_bits") or qc.get("w_bit")
        return f"{method}-{bits}bit" if bits else method
    dtype = config.get("torch_dtype")
    return _DTYPE_SLUG.get(str(dtype), str(dtype)) if dtype else None


def _parameter_size(snapshot: Path, config: dict) -> Optional[str]:
    if config.get("quantization_config"):
        return None  # total_size is the packed size; params not derivable
    bpp = _BYTES_PER_PARAM.get(str(config.get("torch_dtype")))
    index = snapshot / "model.safetensors.index.json"
    if not bpp or not index.is_file():
        return None
    try:
        with open(index) as f:
            total = (json.load(f).get("metadata") or {}).get("total_size")
    except (OSError, ValueError):
        return None
    if not total:
        return None
    params = total / bpp / 1e9
    return f"{params:.1f}B" if params >= 1 else f"{params * 1000:.0f}M"


def _shard_key(p: Path) -> tuple:
    """(part, of) from a sharded filename; unsharded sorts first."""
    m = re.search(r"(\d+)-of-(\d+)", p.name)
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def _weight_files(snapshot: Path) -> list:
    """The snapshot's weight files, in the repo's own order — the identity
    is the first one returned. The repo's `weight_map` index is
    authoritative for the shard set and order; without it, the canonical
    single-file names, then sharded files by shard NUMBER (a filename sort
    would crown `optimizer.bin` — `o` < `p` — over the real weights)."""
    idx = snapshot / "model.safetensors.index.json"
    if idx.is_file():
        try:
            wm = json.loads(idx.read_text()).get("weight_map", {})
        except (OSError, ValueError):
            wm = {}
        if wm:
            return [snapshot / k for k in dict.fromkeys(wm.values())]
    for single in ("model.safetensors", "consolidated.safetensors"):
        if (p := snapshot / single).is_file():
            return [p]
    shards = sorted(
        list(snapshot.glob("model-*-of-*.safetensors"))
        + list(snapshot.glob("pytorch_model-*-of-*.bin")),
        key=_shard_key,
    )
    if shards:
        return shards
    p = snapshot / "pytorch_model.bin"
    return [p] if p.is_file() else []


def describe(snapshot: Path) -> dict:
    """{"files": [{file, sha256, size_bytes}], "details": {...}} for a
    snapshot dir. Weight files come from the repo's own weight map (or
    shard number), so shard 1 is first — its hash is the artifact's
    identity (the "digest pins the weights file" convention); `files`
    carries every shard for catalog_artifact_files."""
    if (snapshot / "adapter_config.json").is_file():
        # PEFT/LoRA adapter: no base-model identity, nothing to report.
        return {"files": [], "details": {}}
    files = []
    for p in _weight_files(snapshot):
        try:
            size = p.stat().st_size  # follows the symlink to the blob
        except OSError:
            size = None
        files.append({"file": p.name, "sha256": _blob_sha256(p), "size_bytes": size})

    config = {}
    cfg = snapshot / "config.json"
    if cfg.is_file():
        try:
            with open(cfg) as f:
                config = json.load(f) or {}
        except (OSError, ValueError):
            config = {}
    details = {
        "quantization": _quantization(config),
        "parameter_size": _parameter_size(snapshot, config),
        "family": config.get("model_type"),
        "context_length": config.get("max_position_embeddings"),
    }
    return {"files": files, "details": details}
