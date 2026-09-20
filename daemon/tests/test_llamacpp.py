"""llama.cpp executor: the model guard, capability reporting and inventory.

Response shapes are taken from a live `llama-server` (build b10759) serving a
Qwen3.6-27B Q4_K_XL GGUF, not from documentation.
"""
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


def _server(chat=None, status=200, props=PROPS_BODY, health=200, seen=None):
    """A stand-in llama-server. `seen` collects the paths requested."""
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request.url.path)
        if request.url.path == "/props":
            return httpx.Response(200, json=props)
        if request.url.path == "/health":
            return httpx.Response(health, json={"status": "ok"})
        if request.url.path == "/v1/models":
            return httpx.Response(200, json=MODELS_BODY)
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


@pytest.mark.asyncio
async def test_inventory_reports_no_hash_and_the_gguf_metadata():
    ex = _client(LlamaCppExecutor("http://llamacpp.test"), _server())
    items = await ex.inventory()

    assert len(items) == 1
    item = items[0]
    assert item["local_name"] == SERVED
    assert item["runtime"] == "llamacpp"     # tagged, or the backend cannot route it
    assert item["sha256"] is None            # name matching, by decision
    assert item["size_bytes"] == 17601570816
    assert item["details"]["quantization_level"] == "Q4_K - Medium"
    assert item["details"]["context_length"] == 262144


@pytest.mark.asyncio
async def test_inventory_never_raises():
    def broken(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    ex = _client(LlamaCppExecutor("http://llamacpp.test"), broken)
    assert await ex.inventory() == []
    assert await ex.list_models() == []
    assert await ex.list_running_models() == []
