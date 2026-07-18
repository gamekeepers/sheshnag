"""
Provider Picker — job-to-worker matching over the curated model catalogue.

`batch.model` is a `model_catalog.id` (a platform slug the user picked),
not a raw runtime string. Matching resolves that id to the catalogue
entry, then requires a worker that:
  - fits the entry's VRAM requirement, and
  - actually hosts the entry's `runtime_model_id` (advertised at
    registration / reported in heartbeats).

Availability is matched on `runtime_model_id` for now; once the daemon
reports per-model digests, tighten to a digest match (the catalogue's
`digest` column is the intended join key) so two workers only satisfy an
entry when the artifact is byte-identical.
"""
from models import ModelCatalog


def get_catalog_entry(db, model_id: str):
    """Resolve a batch's model id to an active, enabled catalogue entry."""
    if not model_id:
        return None
    return db.query(ModelCatalog).filter(
        ModelCatalog.id == model_id,
        ModelCatalog.enabled.is_(True),
        ModelCatalog.status == "active",
    ).first()


def is_model_supported(db, model_id: str) -> bool:
    """True if `model_id` is a selectable catalogue entry."""
    return get_catalog_entry(db, model_id) is not None


def get_model_vram(db, model_id: str):
    """Required VRAM (GB) for a catalogue entry, or None if unknown."""
    entry = get_catalog_entry(db, model_id)
    return entry.vram_gb if entry else None


class ProviderPicker:
    """
    Matches a polling worker to the best available batch.

    Filter: worker must fit the batch model's VRAM (when known) AND host
    the model's runtime artifact.
    Rank:
      1. Prefer batches whose model is already loaded (in VRAM) here.
      2. Fall back to oldest compatible batch (FIFO).
    """

    def find_best_batch(self, db, worker, available_batches):
        if not worker or not available_batches:
            return None

        loaded = set(worker.loaded_model_names())      # runtime ids in VRAM
        advertised = worker.advertised_model_names()   # runtime ids hosted
        vram = worker.vram_total_gb                     # None until first heartbeat

        loaded_matches = []
        other_matches = []
        for batch in available_batches:
            entry = get_catalog_entry(db, batch.model)
            if entry is None:
                continue  # unschedulable — should have failed validation

            # VRAM fit (skip the check until we have a heartbeat)
            required = entry.vram_gb or 0
            if vram is not None and vram < required:
                continue

            rmid = entry.runtime_model_id
            if rmid not in advertised:
                continue  # worker can't serve this artifact

            if rmid in loaded:
                loaded_matches.append(batch)
            else:
                other_matches.append(batch)

        if loaded_matches:
            return loaded_matches[0]
        if other_matches:
            return other_matches[0]
        return None


picker = ProviderPicker()
