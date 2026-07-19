"""
Provider Picker — Modular job-to-provider matching.

Replace this class to upgrade matching logic in the future
(pricing, geo-proximity, reliability scores, etc.)
"""

MODEL_VRAM_REQUIREMENTS = {
    "mistral-7b":          16,
    "mistral-7b-instruct": 16,
    "llama-3-8b":          18,
    "llama-3.1-8b":        18,
    "llama-3-70b":         80,
    "qwen2-7b":            16,
}


def get_model_vram(model_name: str) -> int | None:
    """Return required VRAM in GB for a model. None if unknown."""
    return MODEL_VRAM_REQUIREMENTS.get(model_name)


def is_model_supported(model_name: str) -> bool:
    """Check if a model is in our supported list."""
    return model_name in MODEL_VRAM_REQUIREMENTS


class ProviderPicker:
    """
    Matches a polling worker to the best available batch.

    Reads the worker's aggregate VRAM (from heartbeats) and the loaded
    flags on its runtime_models rows.

    Filter: worker VRAM must fit the batch's model.
    Rank:
      1. Prefer batches whose model is already loaded on this worker.
      2. Fall back to oldest compatible batch (FIFO).
    """

    def find_best_batch(self, worker, available_batches):
        if not worker or not available_batches:
            return None

        loaded = worker.loaded_model_names()
        vram = worker.vram_total_gb or 0

        compatible = []
        for batch in available_batches:
            required = get_model_vram(batch.model) or 0
            if vram >= required:
                compatible.append(batch)

        if not compatible:
            return None

        already_loaded = [b for b in compatible if b.model in loaded]
        if already_loaded:
            return already_loaded[0]

        return compatible[0]


picker = ProviderPicker()
