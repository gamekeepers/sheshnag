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
import re
import struct
import sys
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.identity_resolver import OllamaName, parse_local_name  # noqa: E402
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


def fetch(entry, client):
    """(meta, artifact_bytes) for an entry pinned to an Ollama-registry blob."""
    name = parse_local_name(entry.get("runtime_model_id") or "")
    if not isinstance(name, OllamaName) or not entry.get("digest"):
        raise LookupError("not a pinned Ollama-registry artifact")
    url = f"https://registry.ollama.ai/v2/{name.namespace}/{name.model}/blobs/sha256:{entry['digest']}"
    with client.stream("GET", url) as r:
        r.raise_for_status()
        size = int(r.headers["content-length"])
        return read_gguf_metadata(_Stream(r.iter_bytes())), size


def set_fields(text: str, entry_id: str, fields: dict) -> str:
    """Set `fields` inside one entry's block, after its `vram_gb:` line."""
    lines = text.splitlines(keepends=True)
    start = next(i for i, l in enumerate(lines) if l.rstrip() == f"- id: {entry_id}")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("- id:")), len(lines))
    block = [l for l in lines[start:end] if not re.match(rf"  ({'|'.join(fields)}):", l)]
    anchor = next(i for i, l in enumerate(block) if l.startswith("  vram_gb:")) + 1
    block[anchor:anchor] = [f"  {k}: {v}\n" for k, v in fields.items()]
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
                continue
            print(f"derive {entry['id']}: weights_gb={weights_gb} kv_bytes_per_token={kv} "
                  f"(arch {meta['general.architecture']}, was vram_gb={entry.get('vram_gb')})")
            text = set_fields(text, entry["id"], {"weights_gb": weights_gb, "kv_bytes_per_token": kv})

    if args.write:
        Path(args.manifest).write_text(text)
        print(f"\nUpdated {args.manifest}.")


if __name__ == "__main__":
    main()
