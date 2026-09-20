#!/usr/bin/env python3
"""
Re-estimate size_gb / vram_gb for vLLM catalogue entries.

One-shot repair after the daemon started reporting the model's TOTAL weight
size (every shard) instead of shard 1's: entries auto-adopted before that
carry a VRAM estimate computed from one shard, so the scheduler happily fits a
70B model on an 8 GB worker. This script sums the shard sizes each worker
row reports for the entry's digest and re-estimates from that total.

Only vLLM entries are touched (entries whose legacy `runtime` is vllm or
that carry a vllm serving profile); curated Ollama/llama.cpp values are
never clobbered.

    python scripts/backfill_vllm_sizes.py            # dry run: show diffs
    python scripts/backfill_vllm_sizes.py --apply    # commit the updates

Requires DATABASE_URL (same as the backend).
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.catalog_service import estimate_vram_gb
from backend.database import get_engine
from backend.models import ModelCatalog, RuntimeModel, ServingProfile


def _norm(d):
    return (d or "").strip().lower().split(":", 1)[-1] or None


def main():
    ap = argparse.ArgumentParser(description="Re-estimate vram_gb/size_gb for vLLM catalogue entries from total shard sizes")
    ap.add_argument("--apply", action="store_true", help="Commit the updates (default: dry run)")
    args = ap.parse_args()

    engine = get_engine()
    from sqlalchemy.orm import sessionmaker
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        rows = db.query(RuntimeModel).filter(
            RuntimeModel.digest.isnot(None), RuntimeModel.files.isnot(None)).all()
        totals = {}
        for r in rows:
            total = sum(f.get("size_bytes") for f in (r.files or []) if f and f.get("size_bytes"))
            if total:
                d = _norm(r.digest)
                if d:
                    totals[d] = max(totals.get(d, 0), total)

        vllm_profiles = {p.catalog_id for p in db.query(ServingProfile)
                         if p.runtime == "vllm"}
        changed = 0
        for e in db.query(ModelCatalog).filter(ModelCatalog.digest.isnot(None)):
            if not (e.runtime == "vllm" or e.id in vllm_profiles):
                continue
            total = totals.get(_norm(e.digest))
            if not total:
                continue
            size_gb = round(total / 1024 ** 3, 2)
            vram_gb = estimate_vram_gb(total)
            if (e.size_gb, e.vram_gb) == (size_gb, vram_gb):
                continue
            changed += 1
            print(f"  {e.id}: size_gb {e.size_gb} -> {size_gb}, vram_gb {e.vram_gb} -> {vram_gb}")
            if args.apply:
                e.size_gb = size_gb
                e.vram_gb = vram_gb

        if args.apply:
            db.commit()
            print(f"\nUpdated {changed} entries.")
        else:
            print(f"\n{changed} entries would change (dry run — pass --apply to commit).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
