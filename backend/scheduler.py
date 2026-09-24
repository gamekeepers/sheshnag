"""
Scheduler — job-to-worker matching over the curated model catalogue.

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
import logging
from dataclasses import dataclass
from typing import Optional

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
    HF repo path to vLLM, an extra alias per box); the scheduler matched the
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
# registers a rule here; the scheduler is never edited.
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


def _hosting_runtime(worker, runtime_model_ids, catalog_digest):
    """The runtime row hosting this artifact, or None if none does.

    `Worker.advertised_models()` flattens every runtime into (name, digest)
    pairs and so cannot answer this — the engine is exactly what it drops.
    What it must not drop is that function's eligibility rules: a quarantined,
    drifted or missing row is not dispatchable, and neither is any row of a
    runtime that is down or draining. Both filters have to be repeated here
    rather than inherited, which is the cost of needing the engine.

    First match wins, and `worker.runtimes` is ordered by `position`, which is
    the order the daemon listed them in its `runtime:` config. When two
    runtimes on one worker answer to the SAME name, that order therefore picks
    the fit rule — `[llamacpp, ollama]` fits the pair against VRAM plus RAM,
    `[ollama, llamacpp]` against VRAM alone. Names normally differ per runtime
    (an Ollama tag against a llama.cpp `--alias`), so the collision needs a
    provider to create it deliberately; selecting by the engine the catalogue
    entry actually profiles would remove the ambiguity.
    """
    for runtime in worker.runtimes:
        if not runtime.schedulable:
            continue
        models = [(m.name, m.digest) for m in runtime.models if m.schedulable]
        if _hosts(models, runtime_model_ids, catalog_digest):
            return runtime
    return None


def _hosting_engine(worker, runtime_model_ids, catalog_digest):
    """The engine name of `_hosting_runtime`, or None."""
    runtime = _hosting_runtime(worker, runtime_model_ids, catalog_digest)
    return runtime.engine if runtime is not None else None


def runtime_has(runtime, needs) -> bool:
    """True when the runtime row advertises every capability in `needs`.

    `needs` empty or None means plain chat, which every runtime serves. A
    row with no capabilities (an older daemon) satisfies nothing else.
    """
    if not needs:
        return True
    caps = runtime.capabilities or {}
    return all(caps.get(key) is True for key in needs)


def can_serve(entry, worker, needs=None) -> bool:
    """Could this worker be given a batch for `entry` that needs `needs`?

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
    runtime = _hosting_runtime(worker, _target_ids(entry), entry.digest)
    if runtime is None:
        return False  # does not host the artifact on any schedulable runtime
    if not runtime_has(runtime, needs):
        return False  # hosts it, but cannot honour what the rows ask for
    rule = FIT_RULES.get(runtime.engine, DEFAULT_FIT_RULE)
    return rule(entry.vram_gb or 0, Capacity.of(worker))


class Scheduler:
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

            if not can_serve(entry, worker, batch.required_capabilities or None):
                continue  # doesn't host the artifact, can't fit it, or lacks a capability

            if _hosts(loaded, _target_ids(entry), entry.digest):
                loaded_matches.append(batch)
            else:
                other_matches.append(batch)

        if loaded_matches:
            return loaded_matches[0]
        if other_matches:
            return other_matches[0]
        return None


scheduler = Scheduler()
