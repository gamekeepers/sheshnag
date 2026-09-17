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
import logging

from identity_resolver import bare_digest
from models import ModelCatalog

logger = logging.getLogger(__name__)

# (runtime_model_id, worker digest, catalogue digest) tuples already warned
# about — a mismatch repeats on every poll, the warning should not.
_warned_mismatches = set()


def get_catalog_entry(db, model_id: str):
    """Resolve a batch's model id to an active, enabled catalogue entry."""
    if not model_id:
        return None
    return db.query(ModelCatalog).filter(
        ModelCatalog.id == model_id,
        ModelCatalog.enabled.is_(True),
        ModelCatalog.status.in_(ModelCatalog.SELECTABLE_STATUSES),
    ).first()


def is_model_supported(db, model_id: str) -> bool:
    """True if `model_id` is a selectable catalogue entry."""
    return get_catalog_entry(db, model_id) is not None


def get_model_vram(db, model_id: str):
    """Required VRAM (GB) for a catalogue entry, or None if unknown."""
    entry = get_catalog_entry(db, model_id)
    return entry.vram_gb if entry else None


# One digest normaliser for the whole backend (identity_resolver.bare_digest):
# Ollama reports bare hex, curated entries may carry a `sha256:` prefix.
_norm_digest = bare_digest


def _hosts(worker_models, runtime_model_ids, catalog_digest) -> bool:
    """Does the worker host this catalogue artifact?

    `runtime_model_ids` is every id the artifact answers to — one per
    serving profile (the same weights are `qwen3:4b` to Ollama and an HF
    repo path to vLLM). Matches on any of them, then enforces digest
    equality **only when both sides carry a digest** (the reproducibility
    guard: same tag + different digest ⇒ not a match). If either digest is
    missing (older daemon, un-pinned catalogue entry, non-Ollama runtime),
    fall back to name equality so mixed-version fleets keep scheduling.
    A name the entry does not profile still matches when its digest equals
    the catalogue's — byte-identical artifact, different served alias: the
    digest IS the identity, the name is just what that box calls it.
    """
    cat = _norm_digest(catalog_digest)
    ids = set(runtime_model_ids)
    for name, digest in worker_models:
        wd = _norm_digest(digest)
        if name not in ids:
            if cat and wd and cat == wd:
                return True
            continue
        if cat and wd and cat != wd:
            # Same tag, different artifact — reject. Say so once: a silent
            # rejection here starves the model with nothing in the logs,
            # e.g. a catalogue pinned to a manifest digest while the daemon
            # reports the file hash (#118 review).
            key = (name, wd, cat)
            if key not in _warned_mismatches:
                _warned_mismatches.add(key)
                logger.warning(
                    "Digest mismatch for %r: worker reports %s…, catalogue "
                    "pins %s… — not scheduling here. Re-pin the catalogue "
                    "(scripts/capture_catalog) if the worker's artifact is "
                    "the intended one.", name, wd[:12], cat[:12],
                )
            continue
        return True
    return False


def _target_ids(entry) -> list:
    """runtime_model_ids across the entry's serving profiles (or legacy pair)."""
    return [rmid for _runtime, rmid in entry.serving_targets()]


def _prefer_bare(names) -> str:
    """First of `names`, or its first bare (no '/') name.

    A vLLM box restarted under --served-model-name advertises the alias AND
    the repo id (daemon/executors/vllm.py reports both rows), and vLLM
    answers only to the alias — paths are never valid body.model values, the
    same rule `_pick_served_alias` applies at adoption (catalog_service.py).
    The repo row predates the alias row, so DB row order alone would keep
    dispatching the repo id (404) after a `--served-model-name` upgrade.
    A box served without an alias advertises the single id==root name, and
    Ollama names carry no '/', so the preference changes nothing for them.
    """
    for name in names:
        if name and "/" not in name:
            return name
    return names[0]


def resolve_runtime_model_id(entry, worker_models) -> str:
    """The runtime_model_id to hand THIS worker for `entry` at dispatch.

    A multi-profile entry answers to several ids (`qwen3:4b` to Ollama, an
    HF repo path to vLLM, an extra alias per box); the picker matched the
    worker on ANY of them, so dispatch must send the one this worker
    actually hosts — not the legacy column. Three ways, in order:

    1. a profiled name the worker advertises (digest-checked);
    2. a name the worker advertises with the entry's exact digest — the
       same artifact under a name the entry does not profile yet;
    3. the legacy column (entry with no profiles yet, or the pre-heartbeat
       worker whose model list is empty: the daemon resolves its own
       runtime's id there).

    Within 1 and 2, `_prefer_bare` decides when the worker advertises both a
    repo id and the alias it is served under: only the alias reaches vLLM.
    """
    if entry is None:
        return None
    cat = _norm_digest(entry.digest)
    ids = set(rmid for _runtime, rmid in entry.serving_targets())
    profiled = []
    for name, digest in worker_models:
        if name not in ids:
            continue
        wd = _norm_digest(digest)
        if cat and wd and cat != wd:
            continue  # same name, different artifact
        profiled.append(name)
    if profiled:
        return _prefer_bare(profiled)
    if cat:
        exact = [name for name, digest in worker_models
                 if _norm_digest(digest) == cat]
        if exact:
            return _prefer_bare(exact)
    return entry.runtime_model_id


def can_serve(entry, advertised_models, vram_total_gb) -> bool:
    """Could a worker with these models and this VRAM run `entry`?

    The single definition of "eligible", shared by the scheduler
    (`find_best_batch`) and the pool-capacity endpoint, so the capacity
    a user is shown can never claim a model the scheduler would refuse
    to dispatch.

    `vram_total_gb` is None until the worker's first heartbeat; as in the
    scheduler, that skips the fit check rather than excluding the worker.
    """
    if entry is None:
        return False
    required = entry.vram_gb or 0
    if vram_total_gb is not None and vram_total_gb < required:
        return False
    return _hosts(advertised_models, _target_ids(entry), entry.digest)


class ProviderPicker:
    """
    Matches a polling worker to the best available batch.

    Filter: worker must fit the batch model's VRAM (when known) AND host
    the model's runtime artifact (name + digest when both known).
    Rank:
      1. Prefer batches whose model is already loaded (in VRAM) here.
      2. Fall back to oldest compatible batch (FIFO).
    """

    def find_best_batch(self, db, worker, available_batches):
        if not worker or not available_batches:
            return None

        loaded = worker.loaded_models()          # (name, digest) in VRAM
        advertised = worker.advertised_models()  # (name, digest) hosted
        vram = worker.vram_total_gb              # None until first heartbeat

        loaded_matches = []
        other_matches = []
        for batch in available_batches:
            entry = get_catalog_entry(db, batch.model)
            if entry is None:
                continue  # unschedulable — should have failed validation

            if not can_serve(entry, advertised, vram):
                continue  # can't fit the model, or doesn't host the artifact

            if _hosts(loaded, _target_ids(entry), entry.digest):
                loaded_matches.append(batch)
            else:
                other_matches.append(batch)

        if loaded_matches:
            return loaded_matches[0]
        if other_matches:
            return other_matches[0]
        return None


picker = ProviderPicker()
