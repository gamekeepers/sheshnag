"""Context-aware VRAM fit (#164).

A model's footprint is a line in context, not a number: the scheduler fits a
batch at the context it needs, with the serving runtime's own KV multiplier,
and a large-context batch is refused a card a small-context one is given.
"""

import pytest

from models import Batch, ModelCatalog, Worker, WorkerRuntime
from scheduler import (
    DEFAULT_KV_ALPHA, KV_ALPHA_FLOOR, MIN_CONTEXT, batch_context, observed_kv_alpha,
    required_vram_gb,
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
# Apple M-series, 24 GiB unified memory, 2026-10-27. Neither model nor
# machine is the one DEFAULT_KV_ALPHA was fitted on (ministral-3:3b, CUDA).

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
GEMMA4_BYTES = 7_381_382_048
# Metal-resident model + KV + compute buffers, summed from llama.cpp's load
# log: /api/ps on this build leaves the KV cache out of size_vram.
GEMMA4_MEASURED_GIB = {8192: 7.944, 32768: 8.366, 131072: 9.632}

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

def test_batch_context_is_the_p99_rounded_up_to_a_power_of_two():
    assert batch_context([]) == MIN_CONTEXT
    assert batch_context([100] * 10) == MIN_CONTEXT
    rows = [5000] * 99 + [200_000]        # one outlier does not size the batch
    assert batch_context(rows) == 8192
    assert batch_context([200_000] * 100) == 262_144
    assert batch_context([200_000] * 100, model_context=131_072) == 131_072


def test_row_estimate_counts_text_not_image_bytes():
    image = {"type": "image_url", "image_url": {"url": "data:image/png;base64," + "A" * 3_000_000}}
    row = {"messages": [{"role": "user", "content": [{"type": "text", "text": "x" * 300}, image]}],
           "max_tokens": 64}
    tokens = estimate_row_tokens("/v1/chat/completions", row)
    assert tokens < TOKENS_PER_IMAGE + 300
    assert estimate_row_tokens("/v1/chat/completions", {"messages": []}) == 512  # default max_tokens


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


def test_context_is_capped_at_the_models_own():
    assert required_vram_gb(_line(context_length=8192), 131_072) == required_vram_gb(_line(context_length=8192), 8192)


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


def test_validation_stores_the_context_and_rejects_one_no_host_can_hold(auth_client, db_session):
    _reset(db_session)
    key = _worker_key(auth_client)
    _register(auth_client, key, hostname="24gb", capabilities={})
    _line_entry(db_session)

    batch_id = _submit_rows(auth_client, 3)
    assert _settle(auth_client, batch_id)["status"] == "validated"
    db_session.expire_all()
    assert db_session.get(Batch, batch_id).context_tokens == MIN_CONTEXT

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
