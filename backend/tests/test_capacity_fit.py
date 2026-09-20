"""Per-runtime capacity fit — `can_serve` and the rule table (issue #103).

Plain stubs rather than database rows: `can_serve` reads only the worker's
GPU inventory, its runtimes' models, and the heartbeat memory figures, so
building those directly keeps each case a statement about the predicate
rather than about SQLAlchemy.
"""
from types import SimpleNamespace

import pytest

from scheduler import (
    Capacity,
    DEFAULT_FIT_RULE,
    FIT_RULES,
    can_serve,
    fit_vram_only,
    fit_vram_plus_ram,
)


def _entry(vram_gb=8.0, runtime_model_id="m:latest", digest=None, runtime="ollama"):
    """A catalogue entry as the scheduler reads it.

    `serving_targets()` is the real contract: an artifact answers to one id per
    serving profile, so the scheduler matches on all of them rather than a single
    column.
    """
    return SimpleNamespace(
        vram_gb=vram_gb, runtime_model_id=runtime_model_id, digest=digest,
        serving_targets=lambda: [(runtime, runtime_model_id)],
    )


def _worker(*, gpus=(), engine="ollama", models=("m:latest",),
            vram_total_gb=None, ram_total_gb=None, ram_available_gb=None):
    """A worker with `gpus` card sizes hosting `models` under one engine."""
    return SimpleNamespace(
        gpus=[SimpleNamespace(vram_gb=g) for g in gpus],
        runtimes=[SimpleNamespace(
            engine=engine,
            # Eligibility defaults: these tests are about capacity fit, so the
            # runtime is up and the rows are not quarantined. The filters
            # themselves are covered in test_worker_inventory.
            schedulable=True,
            models=[SimpleNamespace(name=m, digest=None, schedulable=True)
                    for m in models],
        )],
        vram_total_gb=vram_total_gb,
        ram_total_gb=ram_total_gb,
        ram_available_gb=ram_available_gb,
    )


# ─── Per-GPU fit, not the machine sum ───────────────────────

def test_multi_gpu_is_not_offered_a_model_larger_than_its_biggest_card():
    """The headline bug: 2 x 12 GB summed to 24 and took a 20 GB model.

    A single-device runtime cannot span cards, so the model fits on neither.
    """
    worker = _worker(gpus=(12.0, 12.0), vram_total_gb=24.0)

    assert can_serve(_entry(vram_gb=20.0), worker) is False


def test_multi_gpu_still_serves_what_one_card_holds():
    worker = _worker(gpus=(12.0, 12.0), vram_total_gb=24.0)

    assert can_serve(_entry(vram_gb=10.0), worker) is True


def test_single_large_card_serves_a_large_model():
    worker = _worker(gpus=(24.0,), vram_total_gb=24.0)

    assert can_serve(_entry(vram_gb=20.0), worker) is True


def test_cards_of_unknown_size_fall_through_to_the_machine_aggregate():
    """A GPU row with no reported size must not fail every fit at zero."""
    worker = _worker(gpus=(None, None), vram_total_gb=24.0)

    assert can_serve(_entry(vram_gb=20.0), worker) is True


# ─── Registration inventory vs the heartbeat ────────────────

def test_registration_inventory_is_enough_before_the_first_heartbeat():
    """Per-GPU rows arrive at registration, so fit works immediately."""
    worker = _worker(gpus=(24.0,), vram_total_gb=None)

    assert can_serve(_entry(vram_gb=20.0), worker) is True


def test_worker_with_no_capacity_at_all_is_excluded():
    """Names the never-heartbeated decision deliberately.

    Previously `vram_total_gb is None` skipped the fit check, so a worker
    that had reported nothing was treated as able to serve anything. Since
    #60 any worker that can state a capacity does so at registration — by
    probing or via DAEMON_VRAM_GB — so silence now means "told us nothing",
    and that is not a reason to hand it a batch.
    """
    worker = _worker(gpus=(), vram_total_gb=None)

    assert can_serve(_entry(vram_gb=8.0), worker) is False


# ─── Hosting the artifact ───────────────────────────────────

def test_not_hosting_the_artifact_is_refused_however_large_the_worker():
    worker = _worker(gpus=(80.0,), models=("something-else:latest",))

    assert can_serve(_entry(vram_gb=1.0), worker) is False


def test_engine_is_taken_from_the_runtime_that_hosts_the_model():
    """Two runtimes, one artifact: the hosting engine picks the rule.

    `Worker.advertised_models()` flattens runtimes and drops the engine, so
    this is the case that would silently use the wrong rule if `can_serve`
    were still matching against that flattened list.
    """
    worker = _worker(gpus=(4.0,), engine="vllm", models=("m:latest",))
    worker.runtimes.insert(0, SimpleNamespace(
        engine="unknown-engine", schedulable=True,
        models=[SimpleNamespace(name="other:latest", digest=None, schedulable=True)],
    ))

    # Resolved to vllm (which hosts it), not the first runtime in the list.
    assert can_serve(_entry(vram_gb=2.0), worker) is True
    assert can_serve(_entry(vram_gb=8.0), worker) is False


# ─── The rule table ─────────────────────────────────────────

def test_known_engines_are_vram_only():
    assert FIT_RULES["ollama"] is fit_vram_only
    assert FIT_RULES["vllm"] is fit_vram_only


def test_unknown_engine_gets_the_conservative_default():
    """An unrecognised runtime must not be over-committed on its name."""
    assert DEFAULT_FIT_RULE is fit_vram_only

    worker = _worker(gpus=(2.0,), engine="some-future-runtime",
                     ram_total_gb=128.0, ram_available_gb=120.0)

    # Plenty of free RAM, but nothing says this engine can use it.
    assert can_serve(_entry(vram_gb=20.0), worker) is False


def test_only_a_declared_split_runtime_gets_the_hybrid_rule():
    """The issue's acceptance case, both halves.

    Same machine, same 2 GB card, same free RAM — offered a model four times
    its VRAM on a runtime whose split is declared, refused on one where it is
    not. Ollama spills silently and picks the split itself, so the platform
    could neither predict the slowdown nor be sure the job fits; vLLM has no
    hybrid mode at all.
    """
    assert FIT_RULES["llamacpp"] is fit_vram_plus_ram

    def worker_on(engine):
        return _worker(gpus=(2.0,), engine=engine,
                       ram_total_gb=128.0, ram_available_gb=120.0)

    entry = _entry(vram_gb=8.0)

    assert can_serve(entry, worker_on("llamacpp")) is True
    assert can_serve(entry, worker_on("ollama")) is False
    assert can_serve(entry, worker_on("vllm")) is False


# ─── The hybrid rule itself ─────────────────────────────────

def test_hybrid_rule_admits_a_model_larger_than_vram():
    """2 GB card + 128 GB RAM: reserve is max(4, 0.2*128) = 25.6 GB,
    leaving 120 - 25.6 = 94.4 usable, so 2 + 94.4 clears 20 GB."""
    cap = Capacity(vram_by_gpu=(2.0,), ram_total_gb=128.0, ram_available_gb=120.0)

    assert fit_vram_plus_ram(20.0, cap) is True
    assert fit_vram_only(20.0, cap) is False


def test_hybrid_rule_reserves_headroom_for_the_rest_of_the_machine():
    """8 GB free on a 16 GB box reserves max(4, 3.2) = 4, leaving 4 usable."""
    cap = Capacity(vram_by_gpu=(2.0,), ram_total_gb=16.0, ram_available_gb=8.0)

    assert fit_vram_plus_ram(6.0, cap) is True     # 2 + 4
    assert fit_vram_plus_ram(7.0, cap) is False


def test_hybrid_rule_gives_no_credit_for_unknown_free_ram():
    """None is unknown, not "all of it" — a platform with no reading must
    not be handed work on the strength of RAM nobody measured."""
    cap = Capacity(vram_by_gpu=(2.0,), ram_total_gb=128.0, ram_available_gb=None)

    assert fit_vram_plus_ram(20.0, cap) is False


def test_hybrid_rule_needs_some_gpu_figure():
    cap = Capacity(ram_total_gb=128.0, ram_available_gb=120.0)

    assert fit_vram_plus_ram(1.0, cap) is False


# ─── Capacity construction ──────────────────────────────────

def test_capacity_sorts_cards_and_drops_unsized_ones():
    worker = _worker(gpus=(12.0, None, 24.0, 0.0), vram_total_gb=36.0,
                     ram_total_gb=64.0, ram_available_gb=40.0)

    cap = Capacity.of(worker)

    assert cap.vram_by_gpu == (24.0, 12.0)
    assert cap.vram_total_gb == 36.0
    assert cap.ram_total_gb == 64.0
    assert cap.ram_available_gb == 40.0


@pytest.mark.parametrize("entry,worker", [
    (None, _worker(gpus=(24.0,))),
    (_entry(), None),
])
def test_missing_entry_or_worker_is_not_servable(entry, worker):
    assert can_serve(entry, worker) is False
