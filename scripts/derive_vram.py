#!/usr/bin/env python3
"""
Derive `weights_gb` and `kv_bytes_per_token` for catalogue entries (#164).

Reads each pinned artifact's GGUF header straight from registry.ollama.ai —
a ranged read of the first few hundred KB of the blob, never the weights —
and applies `backend/vram_profile.py`. The artifact's size comes from the
same response, so nothing is typed in.

    python scripts/derive_vram.py                     # print what would change
    python scripts/derive_vram.py --write             # update the manifest in place
    python scripts/derive_vram.py --only gemma4-12b-q4km

Edits the two fields as text inside each entry rather than re-dumping the
YAML, so the manifest's comments and layout survive. Entries that cannot be
derived (no digest, not on the Ollama registry, an attention type the
formula does not cover) are reported and left alone: the scheduler then
falls back to their `vram_gb`.
"""
import argparse
import json
import os
import re
import struct
import sys
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.identity_resolver import (  # noqa: E402
    MANIFEST_ACCEPT, OllamaName, model_layer_digests, parse_local_name,
)
from backend.vram_profile import UnsupportedArchitecture, vram_profile  # noqa: E402

MANIFEST = ROOT / "backend" / "catalog" / "models.yaml"

# GGUF value types -> struct format; 8 is a string, 9 an array.
_SCALARS = {0: "B", 1: "b", 2: "H", 3: "h", 4: "I", 5: "i", 6: "f", 7: "?", 10: "Q", 11: "q", 12: "d"}


class _Stream:
    """Sequential reader over an HTTP body, pulling chunks only as needed."""

    def __init__(self, chunks):
        self._chunks = chunks
        self._buf = b""

    def read(self, n):
        while len(self._buf) < n:
            self._buf += next(self._chunks)
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def unpack(self, fmt):
        return struct.unpack("<" + fmt, self.read(struct.calcsize(fmt)))[0]

    def string(self):
        return self.read(self.unpack("Q")).decode("utf-8", "replace")

    def value(self, kind):
        if kind == 8:
            return self.string()
        if kind == 9:
            item, count = self.unpack("I"), self.unpack("Q")
            return [self.value(item) for _ in range(count)]
        return self.unpack(_SCALARS[kind])


def read_gguf_metadata(stream) -> dict:
    """Every metadata key before the first `tokenizer.*` one.

    The architecture keys precede the tokenizer's vocabulary arrays, which run
    to megabytes — stopping there is what keeps this a header read.
    """
    if stream.read(4) != b"GGUF":
        raise ValueError("not a GGUF file")
    stream.unpack("I")                       # version
    stream.unpack("Q")                       # tensor count
    meta = {}
    for _ in range(stream.unpack("Q")):
        key = stream.string()
        if key.startswith("tokenizer."):
            break
        meta[key] = stream.value(stream.unpack("I"))
    return meta


# Manifest layers Ollama loads into memory with the model: the weights and a
# vision projector (llama-server --mmproj). A `draft` layer is not loaded —
# Ollama 0.32 starts llama-server with no --model-draft — so it is not counted.
_LOADED_LAYERS = ("application/vnd.ollama.image.model", "application/vnd.ollama.image.projector")
_PROJECTOR = "application/vnd.ollama.image.projector"


def _local_manifests():
    """Every Ollama manifest on this machine, from the usual models directories."""
    for root in (os.environ.get("OLLAMA_MODELS"), os.path.expanduser("~/.ollama/models"),
                 "/usr/share/ollama/.ollama/models"):
        manifests = Path(root) / "manifests" if root else None
        if manifests and manifests.is_dir():
            for path in manifests.rglob("*"):
                if path.is_file():
                    try:
                        yield json.loads(path.read_text())
                    except (OSError, ValueError):
                        continue


def pinned_manifest(name, digest, client):
    """(manifest whose model layer IS the pin, or None; the tag's current manifest).

    The registry serves manifests by tag, and a tag moves when Ollama
    republishes it, so the current one may describe newer weights and a
    different projector. A machine that pulled the pinned version still has
    its manifest, so that is the second place to look.
    """
    current = None
    r = client.get(name.manifest_url, headers={"Accept": MANIFEST_ACCEPT})
    if r.status_code == 200:
        current = r.json()
        if digest in model_layer_digests(current):
            return current, current
    for manifest in _local_manifests():
        if digest in model_layer_digests(manifest):
            return manifest, current
    return None, current


def artifact_bytes(entry, name, client) -> int:
    """Every byte Ollama loads for the pinned artifact: weights + projector.

    A multi-file artifact sized by its weights blob alone would under-count —
    a gemma3:12b projector is 854 MB — and override a scalar `vram_gb` with
    an underestimate. When the pinned manifest cannot be found and the model
    has, or may have, more than one file, the entry is skipped instead.
    """
    digest = entry["digest"]
    manifest, current = pinned_manifest(name, digest, client)
    if manifest is not None:
        return sum(int(l.get("size") or 0) for l in manifest.get("layers", [])
                   if l.get("mediaType") in _LOADED_LAYERS)
    may_have_more = (
        entry.get("files")
        or (entry.get("capabilities") or {}).get("vision")
        or any(l.get("mediaType") == _PROJECTOR for l in (current or {}).get("layers", []))
    )
    if may_have_more:
        raise LookupError(
            "pinned manifest is no longer on the registry or this machine, and the model "
            "loads more than its weights file (projector); re-pin it or pull it locally"
        )
    return None  # a single-file artifact: its blob's own length is the whole of it


def fetch(entry, client):
    """(meta, artifact_bytes) for an entry pinned to an Ollama-registry blob."""
    name = parse_local_name(entry.get("runtime_model_id") or "")
    if not isinstance(name, OllamaName) or not entry.get("digest"):
        raise LookupError("not a pinned Ollama-registry artifact")
    size = artifact_bytes(entry, name, client)
    url = f"https://registry.ollama.ai/v2/{name.namespace}/{name.model}/blobs/sha256:{entry['digest']}"
    with client.stream("GET", url) as r:
        r.raise_for_status()
        meta = read_gguf_metadata(_Stream(r.iter_bytes()))
        return meta, size if size is not None else int(r.headers["content-length"])


def set_fields(text: str, entry_id: str, fields: dict) -> str:
    """Set `fields` inside one entry's block, after its `vram_gb:` line.

    A field set to None is removed: an entry that can no longer be derived
    must not keep a line it was given before, or the scheduler trusts it over
    the scalar.
    """
    lines = text.splitlines(keepends=True)
    start = next(i for i, l in enumerate(lines) if l.rstrip() == f"- id: {entry_id}")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("- id:")), len(lines))
    block = [l for l in lines[start:end] if not re.match(rf"  ({'|'.join(fields)}):", l)]
    anchor = next(i for i, l in enumerate(block) if l.startswith("  vram_gb:")) + 1
    block[anchor:anchor] = [f"  {k}: {v}\n" for k, v in fields.items() if v is not None]
    return "".join(lines[:start] + block + lines[end:])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", default=str(MANIFEST))
    ap.add_argument("--only", nargs="*", help="entry ids (default: every entry)")
    ap.add_argument("--write", action="store_true", help="update the manifest in place")
    args = ap.parse_args()

    text = Path(args.manifest).read_text()
    entries = yaml.safe_load(text)
    with httpx.Client(follow_redirects=True, timeout=30.0) as client:
        for entry in entries:
            if args.only and entry["id"] not in args.only:
                continue
            try:
                meta, size = fetch(entry, client)
                weights_gb, kv = vram_profile(meta, size)
            except (LookupError, UnsupportedArchitecture, httpx.HTTPError, KeyError, ValueError) as exc:
                if isinstance(exc, httpx.HTTPStatusError):  # the signed CDN URL is noise
                    exc = f"registry blob: HTTP {exc.response.status_code}"
                print(f"skip   {entry['id']}: {exc}")
                text = set_fields(text, entry["id"], {"weights_gb": None, "kv_bytes_per_token": None})
                continue
            print(f"derive {entry['id']}: weights_gb={weights_gb} kv_bytes_per_token={kv} "
                  f"(arch {meta['general.architecture']}, was vram_gb={entry.get('vram_gb')})")
            text = set_fields(text, entry["id"], {"weights_gb": weights_gb, "kv_bytes_per_token": kv})

    if args.write:
        Path(args.manifest).write_text(text)
        print(f"\nUpdated {args.manifest}.")


if __name__ == "__main__":
    main()
