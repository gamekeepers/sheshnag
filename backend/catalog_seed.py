"""
Seed the model catalogue (tier-1, platform-curated) on startup.

Idempotent: only inserts rows whose (runtime, runtime_model_id) is not
already present, so re-runs and admin-added entries are never clobbered.

Digests are left NULL for now — the daemon does not yet report per-model
digests, so availability matching falls back to runtime_model_id (see
provider_picker). Fill `digest` here once digest reporting lands to get
the full reproducibility guard.

`runtime_model_id` is the exact string the runtime expects — Ollama tags
here. `id` is a stable, human-readable platform slug (safe to expose as
the value users put in `body.model`).
"""
import logging

from database import SessionLocal
from models import ModelCatalog

logger = logging.getLogger(__name__)

# id (slug) → catalogue attributes. Seeded from the former
# MODEL_VRAM_REQUIREMENTS, re-keyed to real Ollama tags.
_SEED = [
    {"id": "mistral-7b-instruct-q4-ollama", "display_name": "Mistral 7B Instruct — Q4_K_M (Ollama)",
     "runtime": "ollama", "runtime_model_id": "mistral:7b", "quantization": "Q4_K_M",
     "vram_gb": 16, "size_gb": 4.4},
    {"id": "llama3-8b-instruct-q4-ollama", "display_name": "Llama 3 8B Instruct — Q4_K_M (Ollama)",
     "runtime": "ollama", "runtime_model_id": "llama3:8b", "quantization": "Q4_K_M",
     "vram_gb": 18, "size_gb": 4.7},
    {"id": "llama3.1-8b-instruct-q4-ollama", "display_name": "Llama 3.1 8B Instruct — Q4_K_M (Ollama)",
     "runtime": "ollama", "runtime_model_id": "llama3.1:8b", "quantization": "Q4_K_M",
     "vram_gb": 18, "size_gb": 4.9},
    {"id": "llama3-70b-instruct-q4-ollama", "display_name": "Llama 3 70B Instruct — Q4_K_M (Ollama)",
     "runtime": "ollama", "runtime_model_id": "llama3:70b", "quantization": "Q4_K_M",
     "vram_gb": 80, "size_gb": 40.0},
    {"id": "qwen2-7b-instruct-q4-ollama", "display_name": "Qwen2 7B Instruct — Q4_K_M (Ollama)",
     "runtime": "ollama", "runtime_model_id": "qwen2:7b", "quantization": "Q4_K_M",
     "vram_gb": 16, "size_gb": 4.4},
]


def seed_model_catalog() -> None:
    """Insert missing tier-1 catalogue entries. Safe to call every startup."""
    db = SessionLocal()
    try:
        added = 0
        for entry in _SEED:
            exists = db.query(ModelCatalog).filter(
                ModelCatalog.runtime == entry["runtime"],
                ModelCatalog.runtime_model_id == entry["runtime_model_id"],
            ).first()
            if exists:
                continue
            db.add(ModelCatalog(**entry))
            added += 1
        if added:
            db.commit()
            logger.info("Seeded %d model_catalog entries", added)
    finally:
        db.close()
