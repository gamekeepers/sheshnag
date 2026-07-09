#!/usr/bin/env python3
"""
Fetch all locally-available ML models from Ollama and HuggingFace caches,
enrich with metadata, and export to CSV.
"""

import csv
import json
import os
import subprocess
import uuid
from datetime import datetime
from pathlib import Path

import requests

# ── helpers ────────────────────────────────────────────────────────────────

def hf_model_info(repo_id: str) -> dict:
    """Fetch metadata from the HuggingFace Hub API."""
    try:
        r = requests.get(
            f"https://huggingface.co/api/models/{repo_id}",
            timeout=10,
            headers={
                "Authorization": f"Bearer {os.environ.get('HF_TOKEN', '')}"
            },
        )
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return {}


def parse_param_size(raw: str) -> int | None:
    """Convert strings like '27.8B' or '12.2M' to an integer."""
    if not raw:
        return None
    raw = raw.strip().upper()
    try:
        if raw.endswith("B"):
            return int(float(raw[:-1]) * 1e9)
        elif raw.endswith("M"):
            return int(float(raw[:-1]) * 1e6)
        else:
            return int(raw.replace(",", ""))
    except (ValueError, TypeError):
        return None


def infer_task_type(details: dict, capabilities: list | None = None) -> str:
    """Infer task type from Ollama / HF metadata."""
    caps = set(capabilities or [])
    if "embedding" in caps or "embeddings" in caps:
        return "embedding"
    if "vision" in caps and "completion" in caps:
        return "vision+text-generation"
    if "generation" in details.get("family", "") or "completion" in caps:
        return "text-generation"
    if "clip" in str(details.get("families", "")).lower():
        return "embedding"
    return "unknown"


def detect_quantization(raw: str) -> str | None:
    """Extract quantisation label from a name or revision string."""
    if not raw:
        return None
    upper = raw.upper()
    for tag in ("FP8", "FP16", "BF16", "Q4_K_M", "Q4_0", "Q5_K_M", "Q8_0",
                "INT4", "INT8", "AWQ", "GPTQ", "AVX2"):
        if tag in upper:
            return tag
    return None


# ── Ollama ────────────────────────────────────────────────────────────────

def fetch_ollama_models() -> list[dict]:
    """Query the live Ollama REST API (http://localhost:11434/api/tags)."""
    rows = []
    try:
        resp = requests.get("http://localhost:11434/api/tags", timeout=10).json()
    except Exception as e:
        print(f"[warn] Ollama fetch failed: {e}")
        return rows

    for m in resp.get("models", []):
        details = m.get("details", {})
        caps = m.get("capabilities", [])
        name = m["name"]
        runtime_model_id = name.split(":")[0] if ":" in name else name
        tag = name.split(":")[-1] if ":" in name else "latest"

        rows.append({
            "id": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"ollama-{name}")),
            "name": name,
            "runtime": "ollama",
            "runtime_model_id": runtime_model_id,
            "revision": tag,
            "task_type": infer_task_type(details, caps),
            "parameter_count": parse_param_size(details.get("parameter_size")),
            "quantization": details.get("quantization_level"),
            "context_length": details.get("context_length"),
            "size_bytes": m.get("size"),
            "license": None,
            "local_path": os.path.expanduser("~/.ollama/models"),
            "status": "available",
            "created_at": m.get("modified_at"),
            "updated_at": m.get("modified_at"),
            "last_used_at": None,
            "model_family": details.get("family"),
            "digest": m.get("digest", "")[:16],
        })

    return rows


# ── HuggingFace ────────────────────────────────────────────────────────────

def fetch_hf_cached_models() -> list[dict]:
    """Scan the local HF cache and attach online metadata where possible."""
    try:
        from huggingface_hub import scan_cache_dir, HfApi
    except ImportError:
        print("[warn] huggingface_hub not installed — skipping HF models")
        return []

    cache = scan_cache_dir()
    rows = []

    for repo in cache.repos:
        # Only model repos, skip datasets / spaces
        if repo.repo_type != "model":
            continue

        for rev in repo.revisions:
            commit_hash = rev.commit_hash[:12]
            refs = list(rev.refs) if hasattr(rev, "refs") and rev.refs else []
            size_on_disk = rev.size_on_disk
            snapshot_path = str(rev.snapshot_path)
            last_access = datetime.fromtimestamp(
                repo.last_accessed
            ).isoformat() if hasattr(repo, "last_accessed") else None

            # Try to grab online metadata for enrichment (skip if only config fetched)
            info = {}
            if size_on_disk > 10_000:
                info = hf_model_info(repo.repo_id)

            rows.append({
                "id": str(uuid.uuid5(
                    uuid.NAMESPACE_DNS,
                    f"hf-{repo.repo_id}-{commit_hash}",
                )),
                "name": repo.repo_id,
                "runtime": "huggingface",
                "runtime_model_id": repo.repo_id,
                "revision": commit_hash + (f" ({', '.join(refs)})" if refs else ""),
                "task_type": info.get("pipeline_tag") or "",
                "parameter_count": parse_param_size(
                    str(info.get("config", {}).get("num_parameters", ""))
                    or str(info.get("cardData", {}).get("num_parameters", ""))
                    or ""
                ),
                "quantization": detect_quantization(repo.repo_id),
                "context_length": info.get("config", {}).get(
                    "max_position_embeddings"
                ) or "",
                "size_bytes": size_on_disk,
                "license": (
                    info.get("tags", [])[-1]
                    if info.get("tags") and info["tags"][-1].startswith("license:")
                    else ""
                ),
                "local_path": snapshot_path,
                "status": "available" if size_on_disk > 5_000 else "not_downloaded",
                "created_at": None,
                "updated_at": datetime.fromtimestamp(
                    rev.last_modified
                ).isoformat()
                if hasattr(rev, "last_modified")
                else None,
                "last_used_at": last_access,
                "model_family": "",
                "digest": commit_hash,
            })

    return rows


# ── main ───────────────────────────────────────────────────────────────────

def main():
    out = Path("/tmp/models_inventory.csv")
    all_rows: list[dict] = []

    # ---------- Ollama ----------
    print("* Fetching Ollama models …")
    all_rows.extend(fetch_ollama_models())
    print(f"  → {len(all_rows)} Ollama model(s)")

    # ---------- HuggingFace ----------
    print("* Scanning HuggingFace cache …")
    hf_count_before = len(all_rows)
    all_rows.extend(fetch_hf_cached_models())
    print(
        f"  → {len(all_rows) - hf_count_before} HuggingFace revision(s)"
    )

    if not all_rows:
        print("[!] No models found.")
        return

    # --------- CSV -----------
    fieldnames = [
        "id",
        "name",
        "runtime",
        "runtime_model_id",
        "revision",
        "task_type",
        "parameter_count",
        "quantization",
        "context_length",
        "size_bytes",
        "license",
        "local_path",
        "status",
        "created_at",
        "updated_at",
        "last_used_at",
    ]

    with open(out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=fieldnames, extrasaction="ignore"
        )
        writer.writeheader()
        for row in all_rows:
            writer.writerow(row)

    print(f"\n✓  Wrote {len(all_rows)} rows to {out}")


if __name__ == "__main__":
    main()
