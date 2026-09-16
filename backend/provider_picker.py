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

Capacity is a *vector* (`Capacity`) and the fit test is a property of the
*runtime* (`FIT_RULES`), not one number compared against another: a
single-device runtime is bounded by the largest card rather than the
machine total, and a runtime that offloads layers is bounded by that plus
free system RAM.
"""
from dataclasses import dataclass
from typing import Optional

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


def _norm_digest(d):
    """Canonicalise a digest for comparison. Ollama reports a bare hex digest
    via /api/tags while curated catalogue entries may carry a `sha256:`
    prefix — strip it (and lowercase) so the same artifact compares equal
    regardless of which path wrote it."""
    if not d:
        return None
    d = str(d).strip().lower()
    return d.split(":", 1)[1] if ":" in d else d


def _hosts(worker_models, runtime_model_id: str, catalog_digest) -> bool:
    """Does the worker host this catalogue artifact?

    Matches on runtime_model_id, then enforces digest equality **only when
    both sides carry a digest** (the reproducibility guard: same tag +
    different digest ⇒ not a match). If either digest is missing (older
    daemon, un-pinned catalogue entry, non-Ollama runtime), fall back to
    name equality so mixed-version fleets keep scheduling.
    """
    cat = _norm_digest(catalog_digest)
    for name, digest in worker_models:
        if name != runtime_model_id:
            continue
        wd = _norm_digest(digest)
        if cat and wd and cat != wd:
            continue  # same tag, different artifact — reject
        return True
    return False


@dataclass(frozen=True)
class Capacity:
    """What one job may use on a worker — a vector, not a scalar.

    A single number cannot express real donated hardware: a single-device
    runtime is bounded by the *largest card*, not the machine total, and a
    runtime that offloads layers is bounded by that plus whatever system
    RAM is genuinely free. "Installed" and "free right now" are different
    questions, so both are carried.

    `vram_by_gpu` comes from the registration inventory and is therefore
    known before the first heartbeat; `vram_total_gb` and `ram_available_gb`
    are heartbeat state and are None until one arrives (and `ram_available_gb`
    stays None on any host that exposes no reading — never treat it as 0).
    """
    vram_by_gpu: tuple = ()
    vram_total_gb: Optional[float] = None
    ram_total_gb: Optional[float] = None
    ram_available_gb: Optional[float] = None

    @classmethod
    def of(cls, worker) -> "Capacity":
        # Cards with no reported size are dropped rather than counted as 0:
        # an inventory of unknown-size GPUs should fall through to the
        # machine aggregate, not fail every fit at zero.
        return cls(
            vram_by_gpu=tuple(sorted(
                (g.vram_gb for g in worker.gpus if g.vram_gb), reverse=True,
            )),
            vram_total_gb=worker.vram_total_gb,
            ram_total_gb=worker.ram_total_gb,
            ram_available_gb=worker.ram_available_gb,
        )


# Headroom kept for the OS and any other tenant of the machine. A fraction
# alone under-reserves on a small host (20% of 8 GB leaves too little for
# Linux and a browser); a fixed floor alone over-reserves on a 512 GB server.
RAM_RESERVE_FLOOR_GB = 4.0
RAM_RESERVE_FRACTION = 0.2


def _largest_gpu_gb(cap: Capacity) -> Optional[float]:
    """The biggest single card, or None when there is no per-GPU inventory."""
    return max(cap.vram_by_gpu) if cap.vram_by_gpu else None


def _usable_ram_gb(cap: Capacity) -> float:
    """Free RAM a job may claim, after headroom. 0 when unknown."""
    if cap.ram_available_gb is None:
        return 0.0
    reserve = max(
        RAM_RESERVE_FLOOR_GB, RAM_RESERVE_FRACTION * (cap.ram_total_gb or 0.0),
    )
    return max(cap.ram_available_gb - reserve, 0.0)


def fit_vram_only(required_gb: float, cap: Capacity) -> bool:
    """One model, one device — the largest card must hold it by itself.

    Summing cards would admit a 20 GB model onto a 2 x 12 GB box, where it
    fits on neither. Falls back to the machine aggregate only when no
    per-GPU inventory exists (a worker registered before those rows did).

    Neither known ⇒ not eligible. That is the deliberate answer to the
    never-heartbeated case: since #60 every worker that can state a capacity
    does so at registration — via probing or DAEMON_VRAM_GB — so a worker
    with neither has genuinely told us nothing, and treating silence as
    "can run anything" is how a batch reaches a machine that cannot run it.
    """
    largest = _largest_gpu_gb(cap)
    if largest is not None:
        return largest >= required_gb
    if cap.vram_total_gb is not None:
        return cap.vram_total_gb >= required_gb
    return False


def fit_vram_plus_ram(required_gb: float, cap: Capacity) -> bool:
    """A runtime that offloads layers to system RAM: VRAM + usable RAM.

    Only for engines where the split is *declared* rather than inferred —
    llama.cpp takes `--n-gpu-layers`, so the platform knows what it is
    asking for. Ollama is deliberately excluded: it decides the split
    itself at load time and spills silently rather than failing, so the
    platform can neither predict the slowdown nor be sure the job fits.
    vLLM is excluded because it has no hybrid mode to fit against — it
    fails outright rather than offloading.
    """
    largest = _largest_gpu_gb(cap)
    if largest is None:
        largest = cap.vram_total_gb
    if largest is None:
        return False
    return largest + _usable_ram_gb(cap) >= required_gb


# Fit is a property of the runtime, not of the scheduler. A new engine
# registers a rule here; the picker is never edited.
FIT_RULES = {
    "ollama": fit_vram_only,
    "vllm": fit_vram_only,
    # No daemon reports this engine yet — the executor is a separate piece of
    # work (#103 non-goal). The row is here so the rule the scheduler applies
    # to a hybrid runtime is settled before one exists, rather than being
    # invented under time pressure the day it lands.
    "llamacpp": fit_vram_plus_ram,
}

# An engine we do not recognise gets the conservative rule, so an unknown
# runtime can never be over-committed on the strength of its name alone.
DEFAULT_FIT_RULE = fit_vram_only


def _hosting_engine(worker, runtime_model_id: str, catalog_digest):
    """The engine of the runtime hosting this artifact, or None if none do.

    `Worker.advertised_models()` flattens every runtime into (name, digest)
    pairs and so cannot answer this — the engine is exactly what it drops.
    """
    for runtime in worker.runtimes:
        models = [(m.name, m.digest) for m in runtime.models]
        if _hosts(models, runtime_model_id, catalog_digest):
            return runtime.engine
    return None


def can_serve(entry, worker) -> bool:
    """Could this worker be given a batch for `entry`?

    The single definition of "eligible", shared by the scheduler
    (`find_best_batch`) and the pool-capacity endpoint, so the capacity a
    user is shown can never claim a model the scheduler would refuse to
    dispatch. Both callers must move together whenever this changes.

    Eligible is necessary, not sufficient: it says a worker *can* run the
    model, not that it is the best place for it. Preferring a faster worker
    is a ranking concern and lives elsewhere.
    """
    if entry is None or worker is None:
        return False
    engine = _hosting_engine(worker, entry.runtime_model_id, entry.digest)
    if engine is None:
        return False  # does not host the artifact on any runtime
    rule = FIT_RULES.get(engine, DEFAULT_FIT_RULE)
    return rule(entry.vram_gb or 0, Capacity.of(worker))


class ProviderPicker:
    """
    Matches a polling worker to the best available batch.

    Filter: `can_serve` — the worker must host the model's runtime artifact
    and satisfy that runtime's fit rule.
    Rank:
      1. Prefer batches whose model is already loaded (in VRAM) here.
      2. Fall back to oldest compatible batch (FIFO).
    """

    def find_best_batch(self, db, worker, available_batches):
        if not worker or not available_batches:
            return None

        loaded = worker.loaded_models()          # (name, digest) in VRAM

        loaded_matches = []
        other_matches = []
        for batch in available_batches:
            entry = get_catalog_entry(db, batch.model)
            if entry is None:
                continue  # unschedulable — should have failed validation

            if not can_serve(entry, worker):
                continue  # doesn't host the artifact, or can't fit it

            if _hosts(loaded, entry.runtime_model_id, entry.digest):
                loaded_matches.append(batch)
            else:
                other_matches.append(batch)

        if loaded_matches:
            return loaded_matches[0]
        if other_matches:
            return other_matches[0]
        return None


picker = ProviderPicker()
