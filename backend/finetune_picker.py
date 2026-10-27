"""
Finetune Picker — job-to-worker matching for LoRA training jobs.

Mirrors provider_picker.py's role for batches, but the fit function is
different: training doesn't care whether a worker already has the base
model loaded in a serving runtime (the engine loads its own copy of the
weights) — it only cares whether the worker has enough VRAM headroom for
training, which runs well above pure inference (base weights + adapter
gradients + optimizer state + activations + fp32 logits).

`_TRAINING_VRAM_MULTIPLIER` is a safety margin over the catalogue's
inference `vram_gb`, calibrated against the week-2 G2 bake-off
(docs/forge/week2-g1-g2-findings.md): bf16 LoRA on Qwen2.5-0.5B-Instruct
with the contract defaults (r16 all-linear, seq 1024, micro-batch 1,
gradient checkpointing) peaked at 4.70 GB under LLaMA-Factory (3.87 GB
under Axolotl) against a 1.5 GB inference figure — 3.13x. Most of that
is fp32 logits over Qwen's 152k vocabulary, which does not grow with
parameter count, so 3.2x overestimates for larger bases; that errs on
the side of not scheduling a job that would OOM. (The earlier 1.3x came
from QLoRA at seq 256 on a 4 GB card — a different method and length.)
"""
from models import ModelCatalog

_TRAINING_VRAM_MULTIPLIER = 3.2


def get_base_model_entry(db, base_model_id: str):
    """Resolve a finetune job's base model id to an active, enabled,
    HF-sourced catalogue entry. Only source_type='huggingface' entries are
    finetune-eligible — Ollama-library entries are pre-quantized serving
    artifacts, not raw weights a training loop can load."""
    if not base_model_id:
        return None
    return db.query(ModelCatalog).filter(
        ModelCatalog.id == base_model_id,
        ModelCatalog.source_type == "huggingface",
        ModelCatalog.enabled.is_(True),
        ModelCatalog.status == "active",
    ).first()


def is_base_model_supported(db, base_model_id: str) -> bool:
    """True if `base_model_id` is a selectable finetune base model."""
    return get_base_model_entry(db, base_model_id) is not None


def training_vram_required(db, base_model_id: str):
    """Estimated VRAM (GB) needed to train against this base model, or
    None if unknown (missing catalogue vram_gb — the fit check then
    skips rather than blocking scheduling on an unknown worker)."""
    entry = get_base_model_entry(db, base_model_id)
    if not entry or not entry.vram_gb:
        return None
    return round(entry.vram_gb * _TRAINING_VRAM_MULTIPLIER, 2)


class FinetunePicker:
    """
    Matches a polling worker to the best available finetune job.

    Filter: worker must fit the job's training VRAM requirement (when
    known). Rank: oldest compatible job (FIFO) — no "already loaded"
    preference, unlike ProviderPicker.
    """

    def find_best_job(self, db, worker, available_jobs):
        if not worker or not available_jobs:
            return None

        vram = worker.vram_total_gb  # None until first heartbeat

        for job in available_jobs:
            required = training_vram_required(db, job.base_model_id)
            if required is not None and vram is not None and vram < required:
                continue  # doesn't fit — skip, try the next job
            return job  # oldest compatible (available_jobs is already FIFO-ordered)

        return None


finetune_picker = FinetunePicker()
