"""llama.cpp executor: the model guard, capability reporting and inventory.

Response shapes are taken from a live `llama-server` (build b10759) serving a
Qwen3.6-27B Q4_K_XL GGUF, not from documentation.
"""
import hashlib
import json

import httpx
import pytest

from daemon.executors.llamacpp import LlamaCppExecutor
from daemon.models import PromptRequest

SERVED = "qwen3.6-27b-q4kxl"

# /v1/models, trimmed to the fields the executor reads. The live server also
# returns an Ollama-shaped `models` array alongside `data`; the executor uses
# `data`, so only that is modelled here.
MODELS_BODY = {
    "object": "list",
    "data": [{
        "id": SERVED,
        "aliases": [SERVED],
        "object": "model",
        "owned_by": "llamacpp",
        "meta": {
            "n_ctx_train": 262144,
            "n_embd": 5120,
            "n_params": 26895998464,
            "size": 17601570816,
            "ftype": "Q4_K - Medium",
        },
    }],
}

PROPS_BODY = {
    "build_info": "b10759-b81c99b47",
    "model_path": "/home/x/models/Qwen3.6-27B-UD-Q4_K_XL.gguf",
    "model_alias": SERVED,
    "total_slots": 4,
}


def _client(ex, handler, base="http://llamacpp.test"):
    ex._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url=base)
    return ex


def _server(chat=None, status=200, props=PROPS_BODY, health=200, seen=None,
            models=None):
    """A stand-in llama-server. `seen` collects the paths requested."""
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request.url.path)
        if request.url.path == "/props":
            return httpx.Response(200, json=props)
        if request.url.path == "/health":
            return httpx.Response(health, json={"status": "ok"})
        if request.url.path == "/v1/models":
            return httpx.Response(200, json=models or MODELS_BODY)
        return httpx.Response(status, json=chat if chat is not None else {})
    return handler


def _prompt(model=SERVED, url="/v1/chat/completions"):
    return PromptRequest(
        custom_id="row-1", method="POST", url=url,
        body={"model": model, "messages": [{"role": "user", "content": "hi"}]},
    )


def _ok(model=SERVED):
    return {
        "model": model,
        "object": "chat.completion",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


# ── The model guard ──────────────────────────────────────────────
#
# llama-server ignores body.model and answers from whatever it loaded,
# returning 200. Without a guard a batch pinned to one artifact is silently
# answered by another.

@pytest.mark.asyncio
async def test_known_model_executes():
    ex = _client(LlamaCppExecutor("http://llamacpp.test"), _server(chat=_ok()))
    await ex.health_check()
    result = await ex.execute(_prompt())
    assert result.is_success
    assert result.response["model"] == SERVED


@pytest.mark.asyncio
async def test_unserved_model_is_rejected_before_the_request():
    seen = []
    ex = _client(LlamaCppExecutor("http://llamacpp.test"),
                 _server(chat=_ok(), seen=seen))
    await ex.health_check()
    seen.clear()

    result = await ex.execute(_prompt(model="some-other-model"))

    assert not result.is_success
    assert "MODEL_MISMATCH" in result.error
    assert seen == []           # no inference paid for a prompt we can't serve


@pytest.mark.asyncio
async def test_echoed_model_mismatch_is_caught_without_a_cache():
    """The server swapped models, or the guard never learned the served set."""
    ex = _client(LlamaCppExecutor("http://llamacpp.test"),
                 _server(chat=_ok(model="something-else")))
    # No health_check(), so _served is None and the pre-check cannot fire.
    result = await ex.execute(_prompt())

    assert not result.is_success
    assert "MODEL_MISMATCH" in result.error
    assert "something-else" in result.error


@pytest.mark.asyncio
async def test_guard_survives_a_failed_model_listing():
    """A listing that fails must not silently disarm the guard."""
    ex = _client(LlamaCppExecutor("http://llamacpp.test"), _server(chat=_ok()))
    await ex.health_check()

    def broken(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(503)
        return httpx.Response(200, json=_ok())

    _client(ex, broken)
    assert await ex.list_models() == []
    result = await ex.execute(_prompt(model="some-other-model"))
    assert "MODEL_MISMATCH" in result.error


# ── Server facts ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_health_check_reads_props_not_version():
    seen = []
    ex = _client(LlamaCppExecutor("http://llamacpp.test"), _server(seen=seen))

    assert await ex.health_check() is True
    assert ex.version == "b10759-b81c99b47"
    assert ex.total_slots == 4
    assert ex.model_path.endswith(".gguf")
    assert "/version" not in seen        # llama-server has no such endpoint


@pytest.mark.asyncio
async def test_unreachable_server_fails_health():
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    ex = _client(LlamaCppExecutor("http://llamacpp.test"), refuse)
    assert await ex.health_check() is False


@pytest.mark.asyncio
async def test_unreadable_props_do_not_fail_a_live_server():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/props":
            return httpx.Response(404)
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(200, json=MODELS_BODY)

    ex = _client(LlamaCppExecutor("http://llamacpp.test"), handler)
    assert await ex.health_check() is True
    assert ex.version is None


# ── Capability gaps the provider chose at launch ─────────────────

@pytest.mark.asyncio
async def test_embeddings_without_the_flag_name_the_cause():
    body = {"error": {"code": 501, "message":
                      "This server does not support embeddings. "
                      "Start it with `--embeddings`"}}
    ex = _client(LlamaCppExecutor("http://llamacpp.test"),
                 _server(chat=body, status=501))
    result = await ex.execute(_prompt(url="/v1/embeddings"))

    assert not result.is_success
    assert "UNSUPPORTED_ENDPOINT" in result.error
    assert "--embeddings" in result.error


# ── Listing and inventory ────────────────────────────────────────

@pytest.mark.asyncio
async def test_served_is_resident():
    """One process holds one model for its lifetime."""
    ex = _client(LlamaCppExecutor("http://llamacpp.test"), _server())
    assert await ex.list_models() == [SERVED]
    assert await ex.list_running_models() == [SERVED]


# Router mode (`--models-dir`) serves a directory and loads entries on demand.
# Each entry carries `status.value`; `meta` is absent until something loads.
# Shape taken from a live router server, not documentation.
ROUTER_BODY = {
    "object": "list",
    "data": [
        {
            "id": "nomic-embed-text",
            "aliases": [],
            "object": "model",
            "owned_by": "llamacpp",
            "status": {"value": "loaded", "args": [], "preset": ""},
        },
        {
            "id": "ggml-org/gemma-3-1b-it-GGUF:Q4_K_M",
            "aliases": [],
            "object": "model",
            "owned_by": "llamacpp",
            "status": {"value": "unloaded", "args": [], "preset": ""},
        },
    ],
}


@pytest.mark.asyncio
async def test_router_reports_only_loaded_models_as_running():
    """A directory of GGUFs is servable; only the loaded one occupies memory."""
    ex = _client(LlamaCppExecutor("http://llamacpp.test"),
                 _server(models=ROUTER_BODY))

    assert await ex.list_models() == [
        "nomic-embed-text", "ggml-org/gemma-3-1b-it-GGUF:Q4_K_M",
    ]
    assert await ex.list_running_models() == ["nomic-embed-text"]


@pytest.mark.asyncio
async def test_router_with_nothing_loaded_reports_none_running():
    body = {"object": "list", "data": [
        dict(ROUTER_BODY["data"][0], status={"value": "unloaded"}),
    ]}
    ex = _client(LlamaCppExecutor("http://llamacpp.test"), _server(models=body))

    assert await ex.list_models() == ["nomic-embed-text"]
    assert await ex.list_running_models() == []


@pytest.mark.asyncio
async def test_heartbeat_listing_refreshes_the_model_guard():
    """
    The heartbeat is the only listing on a schedule, so it carries the
    guard's cache. A provider who restarts llama-server on a different GGUF
    would otherwise be rejected for the rest of the daemon's life against
    names the old process held.
    """
    current = {"body": MODELS_BODY}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/props":
            return httpx.Response(200, json=PROPS_BODY)
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/v1/models":
            return httpx.Response(200, json=current["body"])
        return httpx.Response(200, json={})

    ex = _client(LlamaCppExecutor("http://llamacpp.test"), handler)
    await ex.list_models()
    assert ex._served == {SERVED}

    current["body"] = {"object": "list", "data": [
        {"id": "other-gguf", "aliases": [], "object": "model"},
    ]}
    await ex.list_running_models()

    assert ex._served == {"other-gguf"}


@pytest.mark.asyncio
async def test_inventory_without_a_models_dir_reports_metadata_but_no_hash():
    """A server the daemon can reach but whose files it cannot see: still
    advertised, because a model that can be served should be visible, but
    unidentifiable — the backend falls back to matching on the name."""
    ex = _client(LlamaCppExecutor("http://llamacpp.test"), _server())
    items = await ex.inventory()

    assert len(items) == 1
    item = items[0]
    assert item["local_name"] == SERVED
    assert item["runtime"] == "llamacpp"     # tagged, or the backend cannot route it
    assert item["sha256"] is None
    assert item["size_bytes"] == 17601570816
    assert item["details"]["quantization_level"] == "Q4_K - Medium"
    assert item["details"]["context_length"] == 262144


def _gguf(tmp_path, name, body=b"weights", sidecar=None):
    f = tmp_path / f"{name}.gguf"
    f.write_bytes(body)
    if sidecar is not None:
        (tmp_path / f"{name}.gguf.json").write_text(json.dumps(sidecar))
    return f


SHA_OF_WEIGHTS = hashlib.sha256(b"weights").hexdigest()


@pytest.mark.asyncio
async def test_sidecar_identifies_the_file_without_reading_it(tmp_path):
    """The staging script hashed the bytes where they already were, so the
    worker reports that hash and the repo they came from — which is what lets
    the backend confirm the entry instead of quarantining it."""
    _gguf(tmp_path, SERVED, sidecar={
        "sha256": "a" * 64, "size": len(b"weights"),
        "source_ref": "unsloth/Some-GGUF",
    })
    ex = _client(LlamaCppExecutor("http://llamacpp.test", models_dir=str(tmp_path)),
                 _server())

    item = (await ex.inventory())[0]
    assert item["sha256"] == "a" * 64
    assert item["details"]["source_ref"] == "unsloth/Some-GGUF"
    assert item["size_bytes"] == len(b"weights")


@pytest.mark.asyncio
async def test_a_hand_staged_file_is_hashed_once_and_remembered(tmp_path):
    """No sidecar — a file copied in by hand. It is read once, and the result
    is written beside it so neither this process nor the next repeats a
    multi-gigabyte read."""
    _gguf(tmp_path, SERVED)
    ex = _client(LlamaCppExecutor("http://llamacpp.test", models_dir=str(tmp_path)),
                 _server())

    item = (await ex.inventory())[0]
    assert item["sha256"] == SHA_OF_WEIGHTS
    assert item["details"].get("source_ref") is None   # nothing says where it came from

    written = json.loads((tmp_path / f"{SERVED}.gguf.json").read_text())
    assert written["sha256"] == SHA_OF_WEIGHTS


@pytest.mark.asyncio
async def test_a_sidecar_for_different_bytes_is_ignored(tmp_path):
    """A model re-staged under the same name leaves a sidecar describing the
    old bytes. Trusting it would publish one artifact's identity for
    another's, so the size has to agree before it is believed."""
    _gguf(tmp_path, SERVED, sidecar={
        "sha256": "b" * 64, "size": 999999, "source_ref": "unsloth/Stale-GGUF",
    })
    ex = _client(LlamaCppExecutor("http://llamacpp.test", models_dir=str(tmp_path)),
                 _server())

    item = (await ex.inventory())[0]
    assert item["sha256"] == SHA_OF_WEIGHTS
    assert item["details"].get("source_ref") is None


@pytest.mark.parametrize("payload", [
    "null",
    "[1, 2, 3]",
    '"just a string"',
    "{}",
    '{"sha256": null, "size": 7}',
    '{"sha256": 12345, "size": 7}',
    '{"sha256": "", "size": 7}',
    '{"sha256": "a", "size": null}',
    '{"sha256": "a"}',
    "not json at all",
])
@pytest.mark.asyncio
async def test_a_sidecar_that_is_not_an_identity_is_ignored(tmp_path, payload):
    """The sidecar is written by another process into a directory this one
    only reads, so every shape it can arrive in has to be survivable.
    Inventory does not raise, and a document without a hash and a matching
    size says nothing about these bytes."""
    (tmp_path / f"{SERVED}.gguf").write_bytes(b"weights")
    (tmp_path / f"{SERVED}.gguf.json").write_text(payload)
    ex = _client(LlamaCppExecutor("http://llamacpp.test", models_dir=str(tmp_path)),
                 _server())

    item = (await ex.inventory())[0]
    # Fell through to reading the bytes, rather than trusting or exploding.
    assert item["sha256"] == SHA_OF_WEIGHTS
    assert item["details"].get("source_ref") is None


@pytest.mark.asyncio
async def test_hashing_is_capped_per_beat(tmp_path):
    """Inventory runs on the heartbeat, and a 17 GB read takes about ninety
    seconds. Several per beat would stall the beat that proves the worker
    alive, so a directory converges over several beats instead."""
    names = [SERVED, "second-gguf"]
    for n in names:
        _gguf(tmp_path, n, body=n.encode())

    def two_models(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [
            {"id": n, "meta": {"size": len(n)}} for n in names
        ]})

    ex = _client(LlamaCppExecutor("http://llamacpp.test", models_dir=str(tmp_path)),
                 two_models)

    first = {i["local_name"]: i["sha256"] for i in await ex.inventory()}
    assert sum(1 for v in first.values() if v) == 1

    second = {i["local_name"]: i["sha256"] for i in await ex.inventory()}
    assert all(second.values())


@pytest.mark.asyncio
async def test_inventory_never_raises():
    def broken(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    ex = _client(LlamaCppExecutor("http://llamacpp.test"), broken)
    assert await ex.inventory() == []
    assert await ex.list_models() == []
    assert await ex.list_running_models() == []


# ── The zero-VRAM warning ────────────────────────────────────────
#
# It exists to name the #53 failure: online, healthy, never assigned. The
# hybrid fit rule makes it false for llama.cpp, which is dispatchable on
# system RAM alone.

def _hb(runtimes):
    from daemon.heartbeat import HeartbeatManager
    return HeartbeatManager(client=None, worker_id="w", runtimes=runtimes)


@pytest.mark.parametrize("runtimes,never", [
    (["ollama"], True),
    (["vllm", "ollama"], True),
    (["llamacpp"], False),
    (["llamacpp", "ollama"], False),
])
def test_zero_vram_warning_matches_the_fit_rule(caplog, runtimes, never):
    import logging
    hb = _hb(runtimes)
    with caplog.at_level(logging.WARNING):
        hb._maybe_warn_zero_vram()

    assert len(caplog.records) == 1
    text = caplog.records[0].message
    assert ("never assign" in text) is never, text
    if not never:
        assert "system RAM" in text


def test_zero_vram_warning_is_said_once():
    hb = _hb(["llamacpp"])
    hb._maybe_warn_zero_vram()
    assert hb._warned_zero_vram
    hb._maybe_warn_zero_vram()   # second call must be a no-op
