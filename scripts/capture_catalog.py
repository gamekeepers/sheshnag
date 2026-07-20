#!/usr/bin/env python3
"""
Capture real digests + metadata for catalogue entries from a live Ollama.

Digests are only knowable after a model is pulled. Run this on a reference
box that has the models pulled; it queries Ollama and fills the catalogue
manifest so seeded entries get a pinned digest (enabling the strict
same-tag/different-digest reproducibility guard) plus quantization, size,
parameter size, and context length.

Two modes:

  ENRICH (default) — fill derivable fields on entries already in the
  manifest. Curation of *which* models exist stays a human allow-list.

      python -m scripts.capture_catalog \
          --ollama http://localhost:11434 \
          --manifest backend/catalog/models.yaml \
          [--only mistral:7b llama3:8b]        # default: every ollama entry

  DISCOVER (--discover) — for models present in Ollama but NOT yet in the
  manifest, append staged stubs (`enabled: false`) with everything Ollama
  can derive pre-filled. The seeder ignores disabled entries, so nothing
  becomes user-selectable until a human reviews the stub, sets/confirms
  `vram_gb` and `display_name`, tidies the `id`, and flips `enabled: true`.

      python -m scripts.capture_catalog --discover \
          --ollama http://localhost:11434 \
          --manifest backend/catalog/models.yaml

VRAM: Ollama doesn't report a static VRAM *requirement* (it = weights +
KV-cache(context) + overhead). By default `vram_gb` is ESTIMATED from disk
size (a starting point to verify). Pass --measure-vram to instead load each
model and read the real footprint from /api/ps `size_vram` (accurate, but
loads each model — needs GPU headroom; falls back to the estimate).

Never removes entries; curation-by-omission is avoided by design.
"""
import argparse
import sys

import httpx
import yaml


def _bytes_to_gb(n):
    return round(n / (1024 ** 3), 2) if n else None


def _bare_digest(d):
    """Canonical stored form: bare lowercase hex, no `sha256:` prefix.
    (The picker normalizes on read, but we keep the manifest consistent.)"""
    if not d:
        return None
    d = str(d).strip().lower()
    return d.split(":", 1)[1] if ":" in d else d


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
            "digest": _bare_digest(m.get("digest")),
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


# VRAM requirement isn't a static field Ollama reports (it = weights +
# KV-cache(context) + overhead). We either MEASURE it (load the model, read
# /api/ps size_vram) or ESTIMATE from disk size. Both are starting points
# the operator should sanity-check.
_VRAM_FACTOR = 1.2       # weights + activation/overhead margin over disk size
_VRAM_OVERHEAD_GB = 0.5


def estimate_vram_gb(size_gb):
    """Rough VRAM starting estimate from disk size. Verify before relying on it."""
    if not size_gb:
        return None
    return round(size_gb * _VRAM_FACTOR + _VRAM_OVERHEAD_GB, 1)


def measure_vram_gb(base_url: str, model: str):
    """Load the model (tiny generate) then read its real VRAM footprint from
    /api/ps. Returns GB or None. LOADS the model into VRAM — needs GPU
    headroom, and the number reflects the context it loads with."""
    base = base_url.rstrip("/")
    try:
        httpx.post(
            f"{base}/api/generate",
            json={"model": model, "prompt": "ok", "stream": False,
                  "options": {"num_predict": 1}},
            timeout=600.0,
        )
    except Exception as e:
        print(f"  (could not warm {model} to measure VRAM: {e})", file=sys.stderr)
        return None
    try:
        r = httpx.get(f"{base}/api/ps", timeout=10.0)
        for m in r.json().get("models", []):
            if m.get("name") == model and m.get("size_vram"):
                return round(m["size_vram"] / 1024 ** 3, 2)
    except Exception as e:
        print(f"  (could not read /api/ps for {model}: {e})", file=sys.stderr)
    return None


def resolve_vram_gb(args, model: str, size_gb):
    """Measured (if --measure-vram, falling back to estimate) else estimated."""
    if getattr(args, "measure_vram", False):
        v = measure_vram_gb(args.ollama, model)
        if v is not None:
            return v, "measured"
    return estimate_vram_gb(size_gb), "estimated"


def _slug(rmid: str) -> str:
    """Draft a catalogue id from a runtime model id, e.g. mistral:7b ->
    mistral-7b-ollama. Human should tidy it before enabling."""
    base = rmid.replace(":", "-").replace("/", "-").replace(".", "-").lower()
    return f"{base}-ollama"


def _enrich(entries, tags, args) -> int:
    """Fill derivable fields on manifest entries already present. Returns
    the number of entries changed."""
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

        # Fill vram_gb only if the operator hasn't set it (never clobber a
        # curated value).
        if e.get("vram_gb") is None:
            vram, how = resolve_vram_gb(args, rmid, updates["size_gb"])
            if vram is not None:
                updates["vram_gb"] = vram
                print(f"    vram_gb={vram} ({how} — verify)")

        entry_changed = False
        for k, v in updates.items():
            if v is not None and e.get(k) != v:
                e[k] = v
                entry_changed = True
        if entry_changed:
            changed += 1
        print(f"  captured {e['id']}: digest={t['digest']} quant={updates['quantization']} ctx={updates.get('context_length')}")
    return changed


def _discover(entries, tags, args) -> int:
    """Append staged (`enabled: false`) stubs for models present in Ollama
    but not yet in the manifest. Returns the number of stubs added.

    Stubs carry everything Ollama can derive; `vram_gb` is a measured
    (--measure-vram) or estimated starting value the operator should verify
    (null disables the scheduler's VRAM filter), and `enabled` is false so
    the seeder ignores them until promoted.
    """
    known_rmids = {e.get("runtime_model_id") for e in entries if e.get("runtime") == "ollama"}
    added = 0
    for rmid, t in sorted(tags.items()):
        if rmid in known_rmids:
            continue
        ctx = fetch_context_length(args.ollama, rmid)
        vram, how = resolve_vram_gb(args, rmid, t["size_gb"])
        entries.append({
            "id": _slug(rmid),
            "display_name": f"TODO: {rmid}",
            "runtime": "ollama",
            "runtime_model_id": rmid,
            "digest": t["digest"],
            "quantization": t["quantization"],
            "parameter_size": t["parameter_size"],
            "context_length": ctx,
            "vram_gb": vram,   # TODO(operator): verify — see stdout for measured/estimated
            "size_gb": t["size_gb"],
            "task_type": "chat",
            "source_type": "ollama-library",
            "source_ref": rmid.split(":")[0],
            "source_revision": None,
            "homepage_url": f"https://ollama.com/library/{rmid.split(':')[0]}",
            "enabled": False,   # staged — review, set vram_gb, then flip to true
        })
        added += 1
        vnote = f"vram_gb={vram} ({how})" if vram is not None else "vram_gb=null"
        print(f"  discovered {rmid} -> staged '{_slug(rmid)}' (enabled:false, {vnote} — verify)")
    return added


def main():
    ap = argparse.ArgumentParser(description="Enrich / discover the model catalogue manifest from a live Ollama")
    ap.add_argument("--ollama", default="http://localhost:11434")
    ap.add_argument("--manifest", default="backend/catalog/models.yaml")
    ap.add_argument("--only", nargs="*", help="ENRICH: runtime_model_ids to capture (default: all ollama entries)")
    ap.add_argument("--discover", action="store_true",
                    help="Append staged stubs (enabled:false) for Ollama models not yet in the manifest")
    ap.add_argument("--measure-vram", action="store_true",
                    help="Measure vram_gb by loading each model and reading /api/ps size_vram "
                         "(accurate but LOADS each model — needs GPU headroom; falls back to an "
                         "estimate). Default: estimate from disk size.")
    args = ap.parse_args()

    with open(args.manifest) as f:
        entries = yaml.safe_load(f) or []

    tags = fetch_tags(args.ollama)
    changed = _discover(entries, tags, args) if args.discover else _enrich(entries, tags, args)

    if changed:
        with open(args.manifest, "w") as f:
            yaml.safe_dump(entries, f, sort_keys=False, default_flow_style=False, allow_unicode=True)
        note = ("Review staged (enabled:false) entries: set vram_gb + display_name, then flip enabled:true."
                if args.discover else "Commit it, then restart the backend to sync.")
        print(f"\nUpdated {args.manifest}. {note}")
    else:
        print("\nNo changes.")


if __name__ == "__main__":
    main()
