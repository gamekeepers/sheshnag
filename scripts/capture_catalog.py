#!/usr/bin/env python3
"""
Capture real digests + metadata for catalogue entries from a live Ollama.

Digests are only knowable after a model is pulled. Run this on a reference
box that has the models pulled; it queries Ollama and fills the catalogue
manifest so seeded entries get a pinned digest (enabling the strict
same-tag/different-digest reproducibility guard) plus quantization, size,
parameter size, and context length.

Usage:
    ollama pull mistral:7b llama3:8b            # ensure the artifacts exist
    python -m scripts.capture_catalog \
        --ollama http://localhost:11434 \
        --manifest backend/catalog/models.yaml \
        [--only mistral:7b llama3:8b]           # default: every ollama entry

Matches manifest entries by `runtime_model_id`; only rewrites fields it can
derive, and only for entries whose model is present in Ollama. Never adds or
removes entries — curation of *which* models exist stays in the manifest.
"""
import argparse
import sys

import httpx
import yaml


def _bytes_to_gb(n):
    return round(n / (1024 ** 3), 2) if n else None


def fetch_tags(base_url: str) -> dict:
    """runtime_model_id -> {digest, size, quantization, parameter_size} from /api/tags."""
    r = httpx.get(f"{base_url.rstrip('/')}/api/tags", timeout=10.0)
    r.raise_for_status()
    out = {}
    for m in r.json().get("models", []):
        name = m.get("name")
        if not name:
            continue
        details = m.get("details") or {}
        out[name] = {
            "digest": ("sha256:" + m["digest"]) if m.get("digest") and not str(m["digest"]).startswith("sha256:") else m.get("digest"),
            "size_gb": _bytes_to_gb(m.get("size")),
            "quantization": details.get("quantization_level"),
            "parameter_size": details.get("parameter_size"),
        }
    return out


def fetch_context_length(base_url: str, model: str):
    """Best-effort context length from /api/show model_info."""
    try:
        r = httpx.post(f"{base_url.rstrip('/')}/api/show", json={"name": model}, timeout=15.0)
        r.raise_for_status()
        info = r.json().get("model_info") or {}
        for k, v in info.items():
            if k.endswith(".context_length"):
                return int(v)
    except Exception as e:
        print(f"  (context length unavailable for {model}: {e})", file=sys.stderr)
    return None


def main():
    ap = argparse.ArgumentParser(description="Enrich the model catalogue manifest from a live Ollama")
    ap.add_argument("--ollama", default="http://localhost:11434")
    ap.add_argument("--manifest", default="backend/catalog/models.yaml")
    ap.add_argument("--only", nargs="*", help="runtime_model_ids to capture (default: all ollama entries)")
    args = ap.parse_args()

    with open(args.manifest) as f:
        entries = yaml.safe_load(f) or []

    tags = fetch_tags(args.ollama)
    want = set(args.only) if args.only else None

    changed = 0
    for e in entries:
        if e.get("runtime") != "ollama":
            continue
        rmid = e.get("runtime_model_id")
        if not rmid or (want and rmid not in want):
            continue
        if rmid not in tags:
            print(f"  skip {rmid}: not present in Ollama (pull it first)")
            continue

        t = tags[rmid]
        updates = {
            "digest": t["digest"],
            "quantization": t["quantization"] or e.get("quantization"),
            "parameter_size": t["parameter_size"] or e.get("parameter_size"),
            "size_gb": t["size_gb"] or e.get("size_gb"),
        }
        ctx = fetch_context_length(args.ollama, rmid)
        if ctx:
            updates["context_length"] = ctx

        for k, v in updates.items():
            if v is not None and e.get(k) != v:
                e[k] = v
                changed = True
        print(f"  captured {e['id']}: digest={t['digest']} quant={updates['quantization']} ctx={updates.get('context_length')}")

    if changed:
        with open(args.manifest, "w") as f:
            yaml.safe_dump(entries, f, sort_keys=False, default_flow_style=False, allow_unicode=True)
        print(f"\nUpdated {args.manifest}. Commit it, then restart the backend to sync.")
    else:
        print("\nNo changes.")


if __name__ == "__main__":
    main()
