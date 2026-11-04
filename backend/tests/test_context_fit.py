"""Context-aware VRAM fit (#164).

A model's footprint is a line in context, not a number: the scheduler fits a
batch at the context it needs, with the serving runtime's own KV multiplier,
and a large-context batch is refused a card a small-context one is given.
"""

import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from models import Batch, ModelCatalog, RuntimeModel, Worker, WorkerRuntime
from scheduler import (
    DEFAULT_KV_ALPHA, KV_ALPHA_FLOOR, MIN_CONTEXT, batch_context, can_serve, fitted_context,
    observed_kv_alpha, required_vram_gb,
)
from services.batch_validator import TOKENS_PER_IMAGE, estimate_row_tokens
from tests.test_capability_gate import _errors, _register, _settle
from tests.test_provider_policy import _submit_rows
from tests.test_worker_poll import _poll, _reset, _worker_key
from vram_profile import UnsupportedArchitecture, vram_profile

GIB = 1024 ** 3


# ─── Derivation, against real artifacts ─────────────────────
#
# Metadata as /api/show (verbose) returns it for two models on disk, and the
# footprint measured loading each at several contexts — Ollama 0.32.14,
# Apple M-series, 24 GiB unified memory. Neither model nor machine is the one
# DEFAULT_KV_ALPHA was fitted on (ministral-3:3b, CUDA).

GEMMA4_12B = {
    "general.architecture": "gemma4",
    "gemma4.block_count": 48,
    "gemma4.embedding_length": 3840,
    "gemma4.attention.head_count": 16,
    "gemma4.attention.head_count_kv": [8, 8, 8, 8, 8, 1] * 8,
    "gemma4.attention.key_length": 512,
    "gemma4.attention.value_length": 512,
    "gemma4.attention.key_length_swa": 256,
    "gemma4.attention.value_length_swa": 256,
    "gemma4.attention.sliding_window": 1024,
    "gemma4.attention.sliding_window_pattern": [True] * 5 + [False],
}
GEMMA4_12B["gemma4.attention.sliding_window_pattern"] *= 8
# The pinned manifest's model layer + vision projector — both loaded
# (llama-server --mmproj); its `draft` layer is not.
GEMMA4_BYTES = 7_381_382_048 + 175_115_584
# Metal-resident model + projector + KV + compute buffers, summed from
# llama.cpp's load log in one session (2026-11-04): /api/ps on this build
# leaves the KV cache out of size_vram.
GEMMA4_MEASURED_GIB = {8192: 7.803, 32768: 8.530, 131072: 9.795}

PHI3_NUEXTRACT = {
    "general.architecture": "phi3",
    "phi3.block_count": 32,
    "phi3.embedding_length": 3072,
    "phi3.attention.head_count": 32,
    "phi3.attention.head_count_kv": 32,
}
PHI3_BYTES = 2_176_176_256
# /api/ps size_vram — linear for this model, R^2 = 1.
PHI3_MEASURED_GIB = {1024: 2.409, 2048: 2.785, 4096: 3.537}


def test_full_attention_kv_matches_the_runtime():
    weights_gb, kv = vram_profile(PHI3_NUEXTRACT, PHI3_BYTES)
    assert kv == 393_216            # 2 * 32 layers * 32 heads * 96 * 2 bytes; measured 394,272
    assert weights_gb == pytest.approx(PHI3_BYTES / GIB + 0.25, abs=0.01)


def test_sliding_window_layers_do_not_grow_with_context():
    """40 of gemma4's 48 layers cache only the window: KV is 8 layers' worth."""
    weights_gb, kv = vram_profile(GEMMA4_12B, GEMMA4_BYTES)
    assert kv == 16_384             # llama.cpp allocates exactly this per cell
    assert weights_gb == pytest.approx(GEMMA4_BYTES / GIB + 0.25 + 0.625, abs=0.01)


@pytest.mark.parametrize("meta, size, measured", [
    (GEMMA4_12B, GEMMA4_BYTES, GEMMA4_MEASURED_GIB),
    (PHI3_NUEXTRACT, PHI3_BYTES, PHI3_MEASURED_GIB),
])
def test_prediction_is_within_20_percent_of_measured(meta, size, measured):
    weights_gb, kv = vram_profile(meta, size)
    entry = ModelCatalog(weights_gb=weights_gb, kv_bytes_per_token=kv, context_length=None)
    for ctx, gib in measured.items():
        predicted = required_vram_gb(entry, ctx, DEFAULT_KV_ALPHA)
        assert abs(predicted - gib) / gib < 0.20, (ctx, predicted, gib)


def test_latent_attention_is_refused_rather_than_guessed():
    with pytest.raises(UnsupportedArchitecture):
        vram_profile({"general.architecture": "glm4moelite", "glm4moelite.attention.kv_lora_rank": 512,
                      "glm4moelite.block_count": 47}, 1)


def test_shipped_manifest_carries_a_footprint_line():
    """The seeded rows were re-derived from their artifacts, not typed in."""
    import yaml
    from catalog_seed import _MANIFEST
    entries = {e["id"]: e for e in yaml.safe_load(open(_MANIFEST))}
    assert entries["gemma4-12b-q4km"]["kv_bytes_per_token"] == 16_384
    assert entries["nomic-embed-text-f16"]["kv_bytes_per_token"] == 0
    assert "weights_gb" not in entries["glm-4-7-flash-latest-q4km"]  # MLA: keeps vram_gb


# ─── The batch's context ────────────────────────────────────

def test_batch_context_is_the_p99_need():
    assert batch_context([]) == 0
    assert batch_context([100] * 10) == 100
    rows = [5000] * 99 + [200_000]        # one outlier does not size the batch
    assert batch_context(rows) == 5000


def test_fitted_context_rounds_up_to_a_power_of_two_within_the_model():
    entry = _line(context_length=131_072)
    assert fitted_context(entry, 100) == MIN_CONTEXT
    assert fitted_context(entry, 5000) == 8192
    assert fitted_context(entry, 200_000) == 131_072          # capped at the model's own
    assert fitted_context(_line(context_length=2048), 100) == 2048


def test_a_batch_without_a_need_runs_at_what_it_was_fitted_at():
    """A batch validated before #164 has NULL context_tokens; it is fitted at
    MIN_CONTEXT, so it must be RUN at MIN_CONTEXT, not the server's default."""
    assert fitted_context(_line(), None) == MIN_CONTEXT


def test_a_scalar_entry_runs_at_the_small_context_its_scalar_describes():
    scalar = ModelCatalog(vram_gb=9.6, context_length=131_072)
    assert fitted_context(scalar, 100_000) == MIN_CONTEXT


def test_row_estimate_counts_text_not_image_bytes():
    image = {"type": "image_url", "image_url": {"url": "data:image/png;base64," + "A" * 3_000_000}}
    row = {"messages": [{"role": "user", "content": [{"type": "text", "text": "x" * 300}, image]}],
           "max_tokens": 64}
    tokens = estimate_row_tokens("/v1/chat/completions", row)
    assert tokens < TOKENS_PER_IMAGE + 300
    assert estimate_row_tokens("/v1/chat/completions", {"messages": []}) == 512  # default max_tokens


def test_row_estimate_counts_tool_schemas_and_calls():
    """Both are rendered into the prompt; a short message with a big schema is a long prompt."""
    schema = {"type": "function", "function": {"name": "f", "parameters": {"description": "x" * 30_000}}}
    call = {"id": "c1", "type": "function", "function": {"name": "f", "arguments": "y" * 30_000}}
    bare = {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 1}
    tools = dict(bare, tools=[schema])
    called = dict(bare, messages=bare["messages"] + [{"role": "assistant", "content": None, "tool_calls": [call]}])
    base = estimate_row_tokens("/v1/chat/completions", bare)
    assert estimate_row_tokens("/v1/chat/completions", tools) > base + 9_000
    assert estimate_row_tokens("/v1/chat/completions", called) > base + 9_000


def test_a_prompt_array_is_independent_prompts_not_one_long_one():
    many = {"prompt": ["x" * 300] * 100, "max_tokens": 0}
    assert estimate_row_tokens("/v1/completions", many) == estimate_row_tokens(
        "/v1/completions", {"prompt": "x" * 300, "max_tokens": 0})
    assert estimate_row_tokens("/v1/completions", {"prompt": list(range(700)), "max_tokens": 0}) == 700
    assert estimate_row_tokens("/v1/completions", {"prompt": [[1] * 50, [1] * 900], "max_tokens": 0}) == 900


def test_max_tokens_zero_is_kept():
    """Prompt scoring (echo, max_tokens=0) generates nothing; it must not be sized as 512 more."""
    scoring = {"prompt": "x" * 3000, "max_tokens": 0, "echo": True}
    assert estimate_row_tokens("/v1/completions", scoring) == 1001
    assert estimate_row_tokens("/v1/chat/completions", {"messages": [], "max_completion_tokens": 0}) == 0
    assert estimate_row_tokens("/v1/chat/completions", {"messages": [], "max_tokens": None}) == 512


# ─── The fit ────────────────────────────────────────────────

def _line(weights_gb=6.0, kv=100_000, context_length=262_144, vram_gb=99.0):
    return ModelCatalog(weights_gb=weights_gb, kv_bytes_per_token=kv,
                        context_length=context_length, vram_gb=vram_gb)


def test_fit_grows_with_context_and_alpha():
    entry = _line()
    small = required_vram_gb(entry, 4096)
    large = required_vram_gb(entry, 131_072)
    assert small == pytest.approx(6.0 + DEFAULT_KV_ALPHA * 100_000 * 4096 / GIB)
    assert large > 2 * small
    assert required_vram_gb(entry, 4096, alpha=4.0) > small     # four parallel slots


def test_an_entry_without_a_line_keeps_its_scalar():
    assert required_vram_gb(ModelCatalog(vram_gb=9.6, context_length=4096), 131_072) == 9.6


def _fixed_worker(card_gb, limit, allocated, engine="llamacpp"):
    """One card, one runtime hosting m:latest, launched with a fixed context."""
    return SimpleNamespace(
        gpus=[SimpleNamespace(vram_gb=card_gb)], vram_total_gb=card_gb,
        ram_total_gb=None, ram_available_gb=None,
        window_start_min=None, window_end_min=None, window_tz=None, max_batch_prompts=None,
        runtimes=[SimpleNamespace(
            engine=engine, schedulable=True, kv_alpha=None, capabilities={},
            models=[SimpleNamespace(name="m:latest", digest=None, schedulable=True,
                                    context_limit=limit, context_allocated=allocated)],
        )],
    )


def _m(**kw):
    entry = _line(**kw)
    entry.runtime, entry.runtime_model_id = "llamacpp", "m:latest"
    return entry


def test_a_fixed_context_runtime_is_fitted_at_what_it_allocated():
    """llama.cpp started with -c 32768 -np 4 holds 32768 cells whatever the batch:
    6 + 1.15 * 100 KB * 32768 = 9.5 GiB. A per-request fit at 4096 (6.4 GiB)
    would put a short batch on an 8 GB card that cannot hold the server's cache."""
    assert not can_serve(_m(), _fixed_worker(8, 8192, 32_768), context=100)
    assert not can_serve(_m(), _fixed_worker(8, 8192, 32_768))            # capacity too
    assert can_serve(_m(), _fixed_worker(12, 8192, 32_768), context=100)


def test_a_fixed_context_runtime_refuses_rows_longer_than_one_request_may_be():
    """Its memory fits, but each slot takes 8192: a 9000-token row cannot run."""
    assert can_serve(_m(), _fixed_worker(24, 8192, 32_768), context=8192)
    assert not can_serve(_m(), _fixed_worker(24, 8192, 32_768), context=9000)


def test_calibration_only_ever_raises_alpha_from_the_floor():
    entry = _line(weights_gb=6.0, kv=100_000)
    ctx = 32_768                                  # KV at alpha 1 = 3.05 GiB
    kv_gb = 100_000 * ctx / GIB
    assert observed_kv_alpha(entry, int((6.0 + 2 * kv_gb) * GIB), ctx) == pytest.approx(2.0)
    # A runtime that leaves the KV cache out of its figure does not shrink alpha.
    assert observed_kv_alpha(entry, int(6.2 * GIB), ctx) == KV_ALPHA_FLOOR
    # Too little KV at this context to say anything.
    assert observed_kv_alpha(entry, int(7 * GIB), 512) is None
    assert observed_kv_alpha(ModelCatalog(vram_gb=9.0), int(9 * GIB), ctx) is None


# ─── End to end ─────────────────────────────────────────────

def _line_entry(db, digest=None):
    """poll-model on a 24 GB card: 6 GiB of weights and 100 KB of KV a token."""
    entry = db.query(ModelCatalog).filter_by(id="poll-model").first()
    if entry is None:
        entry = ModelCatalog(id="poll-model", display_name="Poll Model", runtime="ollama",
                             runtime_model_id="poll-model:latest", enabled=True, status="active")
        db.add(entry)
    entry.vram_gb, entry.weights_gb, entry.kv_bytes_per_token = 8.0, 6.0, 100_000
    entry.context_length, entry.digest = 262_144, digest
    db.commit()
    return entry


def _queued(db, batch_id, context_tokens):
    db.add(Batch(id=batch_id, endpoint="/v1/chat/completions", input_file_id="f-ctx",
                 status="validated", model="poll-model", context_tokens=context_tokens))
    db.commit()


def test_a_large_context_batch_is_refused_a_card_a_small_one_is_given(auth_client, db_session):
    _reset(db_session)
    key = _worker_key(auth_client)
    worker_id = _register(auth_client, key, hostname="24gb", capabilities={})
    _line_entry(db_session)
    # 6 + 1.15 * 100 KB * 262144 = 34 GiB: no 24 GB card holds it.
    _queued(db_session, "batch-long", 262_144)
    assert _poll(auth_client, key, worker_id).json()["job"] is None

    # 6 + 1.15 * 100 KB * 8192 = 6.9 GiB: the same card takes it, told the context.
    _queued(db_session, "batch-short", 8192)
    job = _poll(auth_client, key, worker_id).json()["job"]
    assert job["job_id"] == "batch-short"
    assert job["num_ctx"] == 8192
    _reset(db_session)


def test_a_batch_from_before_164_is_run_at_the_context_it_was_fitted_at(auth_client, db_session):
    """NULL need is fitted at MIN_CONTEXT, so the daemon is told MIN_CONTEXT —
    not left to Ollama's server default, which may allocate more."""
    _reset(db_session)
    key = _worker_key(auth_client)
    worker_id = _register(auth_client, key, hostname="24gb", capabilities={})
    _line_entry(db_session)
    _queued(db_session, "batch-legacy", None)
    assert _poll(auth_client, key, worker_id).json()["job"]["num_ctx"] == MIN_CONTEXT
    _reset(db_session)


def test_validation_stores_the_context_and_rejects_one_no_host_can_hold(auth_client, db_session):
    _reset(db_session)
    key = _worker_key(auth_client)
    _register(auth_client, key, hostname="24gb", capabilities={})
    _line_entry(db_session)

    batch_id = _submit_rows(auth_client, 3)
    assert _settle(auth_client, batch_id)["status"] == "validated"
    db_session.expire_all()
    # The need, not a rounded context: 8 template + 1 text + 512 to generate.
    assert db_session.get(Batch, batch_id).context_tokens == 521

    # A model no online host fits even at the smallest context leaves nothing
    # to judge by — the same as no host online — so the batch queues.
    db_session.query(ModelCatalog).filter_by(id="poll-model").update({"kv_bytes_per_token": 50_000_000})
    db_session.commit()
    assert _settle(auth_client, _submit_rows(auth_client, 3))["status"] == "validated"
    _reset(db_session)


def test_context_too_large_for_every_online_host_fails_at_validation(auth_client, db_session):
    _reset(db_session)
    key = _worker_key(auth_client)
    _register(auth_client, key, hostname="24gb", capabilities={})
    _line_entry(db_session)
    # 600 KB of prompt is ~200k tokens, so the batch is fitted at 262144:
    # 6 + 1.15 * 100 KB * 262144 = 34 GiB on a 24 GB card that holds the model at 4096.
    long_prompt = "x" * (3 * 200_000)
    up = auth_client.post("/v1/files", files={"file": ("in.jsonl", (
        '{"custom_id": "r1", "method": "POST", "url": "/v1/chat/completions", "body": '
        '{"model": "poll-model", "messages": [{"role": "user", "content": "' + long_prompt + '"}]}}\n'
    ).encode(), "application/jsonl")})
    cr = auth_client.post("/v1/batches", json={"input_file_id": up.json()["id"], "endpoint": "/v1/chat/completions"})
    body = _settle(auth_client, cr.json()["id"])
    assert body["status"] == "failed"
    assert _errors(body)[0]["code"] == "context_too_large"
    _reset(db_session)


def _register_card(auth_client, key, hostname, vram_gb):
    resp = auth_client.post("/workers/register", headers={"Authorization": f"Bearer {key}"}, json={
        "hostname": hostname,
        "gpus": [{"index": 0, "name": "card", "vram_gb": vram_gb}],
        "runtimes": [{"type": "ollama", "endpoint": "localhost", "models": ["poll-model:latest"],
                      "capabilities": {}, "version": "0.32.14"}],
    })
    assert resp.status_code == 200, resp.text
    return resp.json()["worker_id"]


def _submit_long_rows(auth_client, n, prompt_bytes):
    line = lambda i: json.dumps({"custom_id": f"r{i}", "method": "POST", "url": "/v1/chat/completions",
                                 "body": {"model": "poll-model",
                                          "messages": [{"role": "user", "content": "x" * prompt_bytes}]}})
    up = auth_client.post("/v1/files", files={"file": (
        "in.jsonl", io.BytesIO(("\n".join(line(i) for i in range(n)) + "\n").encode()), "application/jsonl")})
    cr = auth_client.post("/v1/batches", json={"input_file_id": up.json()["id"], "endpoint": "/v1/chat/completions"})
    return cr.json()["id"]


def test_size_and_context_must_be_satisfied_by_the_same_worker(auth_client, db_session):
    """One worker takes any size but lacks the memory at this context; the
    other has the memory but caps batches below this file. Neither can be
    offered it, so it must not validate."""
    _reset(db_session)
    key = _worker_key(auth_client)
    _line_entry(db_session)
    _register_card(auth_client, key, "no-cap-16gb", 16.0)
    capped = _register_card(auth_client, key, "capped-24gb", 24.0)
    db_session.get(Worker, capped).max_batch_prompts = 2
    db_session.commit()
    # ~100k tokens a row, fitted at 131072: 6 + 1.15 * 100 KB * 131072 = 20 GiB.
    body = _settle(auth_client, _submit_long_rows(auth_client, 3, 3 * 100_000))
    assert body["status"] == "failed"
    err = _errors(body)[0]
    assert err["code"] == "batch_too_large"
    assert "at most 2 requests" in err["message"]
    _reset(db_session)


def test_heartbeat_records_a_fixed_runtimes_launch_context(auth_client, db_session):
    _reset(db_session)
    key = _worker_key(auth_client)
    _line_entry(db_session)
    worker_id = _register(auth_client, key, hostname="llama-box", capabilities={})
    resp = auth_client.post(f"/workers/{worker_id}/heartbeat", headers={"Authorization": f"Bearer {key}"}, json={
        "inventory": [{"local_name": "poll-model:latest", "runtime": "ollama",
                       "context_limit": 8192, "context_allocated": 32_768}],
    })
    assert resp.status_code == 200, resp.text
    db_session.expire_all()
    row = db_session.query(RuntimeModel).filter_by(name="poll-model:latest").one()
    assert (row.context_limit, row.context_allocated) == (8192, 32_768)

    # A batch whose rows need more than one request may hold is not offered.
    _queued(db_session, "batch-too-long-for-a-slot", 9000)
    assert _poll(auth_client, key, worker_id).json()["job"] is None
    _queued(db_session, "batch-fits-a-slot", 8000)
    assert _poll(auth_client, key, worker_id).json()["job"]["job_id"] == "batch-fits-a-slot"
    _reset(db_session)


def test_heartbeat_calibrates_the_runtimes_alpha(auth_client, db_session):
    _reset(db_session)
    key = _worker_key(auth_client)
    digest = "c" * 64
    _line_entry(db_session, digest=digest)
    worker_id = _register(auth_client, key, hostname="2-slots", capabilities={})
    ctx = 32_768
    kv_gb = 100_000 * ctx / GIB

    def beat(size_vram_gib):
        resp = auth_client.post(f"/workers/{worker_id}/heartbeat", headers={"Authorization": f"Bearer {key}"}, json={
            "inventory": [{"local_name": "poll-model:latest", "sha256": digest, "loaded": True,
                           "runtime": "ollama", "size_vram_bytes": int(size_vram_gib * GIB),
                           "loaded_context": ctx}],
        })
        assert resp.status_code == 200, resp.text
        db_session.expire_all()
        return db_session.query(WorkerRuntime).filter_by(worker_id=worker_id).one().kv_alpha

    assert beat(6.0 + 2 * kv_gb) == pytest.approx(2.0, abs=0.01)   # two parallel slots
    assert beat(6.1) == KV_ALPHA_FLOOR                             # under-reported: floor
    _reset(db_session)


# ─── scripts/derive_vram.py: every file the runtime loads ────

def _derive():
    path = Path(__file__).resolve().parents[2] / "scripts" / "derive_vram.py"
    spec = importlib.util.spec_from_file_location("derive_vram", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _registry(manifest):
    return httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=manifest)))


def _layer(kind, digest, size):
    return {"mediaType": f"application/vnd.ollama.image.{kind}", "digest": f"sha256:{digest}", "size": size}


@pytest.fixture
def derive(monkeypatch):
    module = _derive()
    monkeypatch.setattr(module, "_local_manifests", lambda: iter(()))  # not this machine's models
    return module


def test_derive_counts_the_projector_with_the_weights(derive):
    manifest = {"layers": [_layer("model", "a" * 64, 7_000), _layer("projector", "b" * 64, 800),
                           _layer("draft", "c" * 64, 450), _layer("license", "d" * 64, 9)]}
    name = derive.parse_local_name("gemma3:12b")
    with _registry(manifest) as client:
        assert derive.artifact_bytes({"digest": "a" * 64}, name, client) == 7_800   # no draft


def test_derive_skips_a_multi_file_artifact_whose_pinned_manifest_is_gone(derive):
    republished = {"layers": [_layer("model", "e" * 64, 7_000), _layer("projector", "b" * 64, 800)]}
    name = derive.parse_local_name("gemma3:12b")
    with _registry(republished) as client:
        with pytest.raises(LookupError):
            derive.artifact_bytes({"digest": "a" * 64}, name, client)


def test_derive_sizes_a_single_file_artifact_by_its_blob(derive):
    republished = {"layers": [_layer("model", "e" * 64, 7_000)]}
    name = derive.parse_local_name("gpt-oss:20b")
    with _registry(republished) as client:
        assert derive.artifact_bytes({"digest": "a" * 64}, name, client) is None   # blob length


def test_derive_removes_a_line_it_can_no_longer_vouch_for(derive):
    text = "- id: m\n  vram_gb: 9.6\n  weights_gb: 8.4\n  kv_bytes_per_token: 65536\n  size_gb: 7\n"
    out = derive.set_fields(text, "m", {"weights_gb": None, "kv_bytes_per_token": None})
    assert out == "- id: m\n  vram_gb: 9.6\n  size_gb: 7\n"
