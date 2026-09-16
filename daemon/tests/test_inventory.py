"""On-disk inventory reporting (#116): manifest-layer file hashes, name
derivation, the /api/tags fallback, and the heartbeat payload."""
import json

import httpx
import pytest

from daemon.executors.ollama import OllamaExecutor
from daemon.executors.vllm import VLLMExecutor
from daemon.heartbeat import HeartbeatManager

WEIGHTS_SHA = "a" * 64
MMPROJ_SHA = "b" * 64


def _write_manifest(root, host, namespace, model, tag, layers):
    d = root / "manifests" / host / namespace / model
    d.mkdir(parents=True, exist_ok=True)
    (d / tag).write_text(json.dumps({
        "schemaVersion": 2,
        "config": {"mediaType": "application/vnd.docker.container.image.v1+json"},
        "layers": layers,
    }))


def _model_layer(sha, size):
    return {
        "mediaType": "application/vnd.ollama.image.model",
        "digest": f"sha256:{sha}",
        "size": size,
    }


def _template_layer():
    return {
        "mediaType": "application/vnd.ollama.image.template",
        "digest": "sha256:" + "c" * 64,
        "size": 100,
    }


# ─── Ollama: manifest scan ───────────────────────────────────

@pytest.mark.asyncio
async def test_ollama_inventory_reads_model_layer_hash(tmp_path):
    """The reported sha256 is the MODEL LAYER digest (== the GGUF file's
    own sha256), never /api/tags' digest (which hashes the manifest)."""
    _write_manifest(
        tmp_path, "registry.ollama.ai", "library", "qwen3", "4b",
        [_template_layer(), _model_layer(WEIGHTS_SHA, 2497280256)],
    )
    ex = OllamaExecutor(models_dir=str(tmp_path))
    ex._client = _api_client({})           # no details available -> None
    items = await ex.inventory()
    assert items == [{
        "local_name": "qwen3:4b",
        "sha256": WEIGHTS_SHA,
        "size_bytes": 2497280256,
        "details": None,
    }]


@pytest.mark.asyncio
async def test_ollama_inventory_namespaced_and_multiple(tmp_path):
    _write_manifest(
        tmp_path, "registry.ollama.ai", "library", "gemma3", "4b",
        [_model_layer(WEIGHTS_SHA, 10)],
    )
    _write_manifest(
        tmp_path, "hf.co", "unsloth", "qwen3-gguf", "Q4_K_M",
        [_model_layer(MMPROJ_SHA, 20)],
    )
    # A community model on the default registry: Ollama renders it
    # `user/model:tag` (host dropped) — the heartbeat's loaded flag and the
    # catalogue's runtime_model_id both join on that exact string.
    _write_manifest(
        tmp_path, "registry.ollama.ai", "someuser", "mymodel", "latest",
        [_model_layer("e" * 64, 30)],
    )
    ex = OllamaExecutor(models_dir=str(tmp_path))
    ex._client = _api_client({})
    names = {i["local_name"] for i in await ex.inventory()}
    assert names == {
        "gemma3:4b",
        "someuser/mymodel:latest",
        "hf.co/unsloth/qwen3-gguf:Q4_K_M",
    }


@pytest.mark.asyncio
async def test_ollama_inventory_skips_junk_manifest(tmp_path):
    _write_manifest(
        tmp_path, "registry.ollama.ai", "library", "good", "latest",
        [_model_layer(WEIGHTS_SHA, 10)],
    )
    bad = tmp_path / "manifests" / "registry.ollama.ai" / "library" / "bad"
    bad.mkdir(parents=True)
    (bad / "latest").write_text("{not json")
    ex = OllamaExecutor(models_dir=str(tmp_path))
    ex._client = _api_client({})
    items = await ex.inventory()
    assert [i["local_name"] for i in items] == ["good:latest"]


@pytest.mark.asyncio
async def test_ollama_inventory_falls_back_to_tags(tmp_path, monkeypatch):
    """Unreadable models dir (daemon runs as a different user) degrades to
    /api/tags names with sha256=None — never a manifest digest passed off
    as a file hash, and never a raised exception."""
    ex = OllamaExecutor(models_dir=str(tmp_path / "nonexistent"))
    monkeypatch.setattr(
        ex, "list_models_detailed",
        # /api/tags digest deliberately present — it must NOT leak into sha256.
        lambda: _async([{"name": "qwen3:4b", "digest": "d" * 64}]),
    )
    # Auto-detection must not pick up the real machine's Ollama store.
    monkeypatch.setattr(ex, "_resolve_models_dir", lambda: None)
    ex._client = _api_client({})
    items = await ex.inventory()
    assert items == [{"local_name": "qwen3:4b", "sha256": None, "size_bytes": None,
                      "details": None}]


def _async(value):
    async def _coro():
        return value
    return _coro()


def _api_client(models, show_calls=None):
    """Mock Ollama API: /api/tags with `details`, /api/show with model_info.
    `models` = {name: {"quantization_level", "parameter_size", "family",
    "context_length"}}. `show_calls` (a list) records /api/show names."""
    def handler(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [
                {"name": n, "digest": "x" * 64,
                 "details": {k: v for k, v in d.items() if k != "context_length"}}
                for n, d in models.items()
            ]})
        if request.url.path == "/api/show":
            name = json.loads(request.content)["model"]
            if show_calls is not None:
                show_calls.append(name)
            d = models.get(name)
            if d is None:
                return httpx.Response(404, json={"error": "not found"})
            return httpx.Response(200, json={"model_info": {
                "general.architecture": "qwen3",
                "qwen3.context_length": d["context_length"],
            }})
        return httpx.Response(404)
    return httpx.AsyncClient(base_url="http://ollama.test", transport=httpx.MockTransport(handler))


# ─── Details for auto-adopt ──────────────────────────────────

@pytest.mark.asyncio
async def test_inventory_attaches_details_and_caches_show_per_hash(tmp_path):
    """quant/params/family from one /api/tags call, context_length from
    /api/show — and /api/show is paid once per artifact, not per beat."""
    _write_manifest(
        tmp_path, "registry.ollama.ai", "library", "qwen3", "4b",
        [_model_layer(WEIGHTS_SHA, 10)],
    )
    show_calls = []
    ex = OllamaExecutor(models_dir=str(tmp_path))
    ex._client = _api_client({
        "qwen3:4b": {"quantization_level": "Q4_K_M", "parameter_size": "4.0B",
                     "family": "qwen3", "context_length": 40960},
    }, show_calls)

    items = await ex.inventory()
    assert items[0]["details"] == {
        "quantization": "Q4_K_M", "parameter_size": "4.0B",
        "family": "qwen3", "context_length": 40960,
    }
    await ex.inventory()
    await ex.inventory()
    assert show_calls == ["qwen3:4b"]          # cached by sha256 after the first beat


@pytest.mark.asyncio
async def test_inventory_details_degrade_without_show(tmp_path):
    """/api/show failing (or the model unknown to the API) must not drop the
    item or raise — details carry whatever /api/tags gave, ctx None."""
    _write_manifest(
        tmp_path, "registry.ollama.ai", "library", "orphan", "1b",
        [_model_layer(MMPROJ_SHA, 10)],
    )
    ex = OllamaExecutor(models_dir=str(tmp_path))
    # /api/tags knows nothing about it -> no details at all -> None
    ex._client = _api_client({})
    items = await ex.inventory()
    assert items[0]["local_name"] == "orphan:1b" and items[0]["details"] is None


# ─── vLLM ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_vllm_inventory_reports_served_name_and_root():
    """Under --served-model-name the served id (what profiles pin and
    dispatch sends) differs from root (the HF path); both rows are
    reported so either convention joins. Identical id/root -> one row."""
    def handler(request):
        assert request.url.path == "/v1/models"
        return httpx.Response(200, json={"data": [
            {"id": "served-alias", "root": "Qwen/Qwen3-4B-Instruct"},
            {"id": "Qwen/Qwen3-8B", "root": "Qwen/Qwen3-8B"},
            {"id": "no-root-model"},
        ]})

    ex = VLLMExecutor(base_url="http://vllm.test")
    ex._client = httpx.AsyncClient(
        base_url="http://vllm.test", transport=httpx.MockTransport(handler),
    )
    items = await ex.inventory()
    assert [i["local_name"] for i in items] == [
        "served-alias", "Qwen/Qwen3-4B-Instruct", "Qwen/Qwen3-8B", "no-root-model",
    ]
    assert all(i["sha256"] is None for i in items)


# ─── Heartbeat payload ───────────────────────────────────────

@pytest.mark.asyncio
async def test_heartbeat_inventory_carries_loaded_flags(monkeypatch):
    monkeypatch.setattr(
        "daemon.heartbeat.get_gpu_utilization",
        lambda: {"utilization": 0.0, "memory_used_gb": 0.0, "memory_total_gb": 24.0},
    )

    async def loaded():
        return ["qwen3:4b"]

    async def inv():
        return [
            {"local_name": "qwen3:4b", "sha256": WEIGHTS_SHA,
             "size_bytes": 1, "runtime": "ollama"},
            {"local_name": "gemma3:4b", "sha256": MMPROJ_SHA,
             "size_bytes": 2, "runtime": "ollama"},
        ]

    manager = HeartbeatManager(
        client=None, worker_id="w1",
        get_loaded_models=loaded, get_inventory=inv,
    )
    payload = await manager._build_payload()
    by_name = {i["local_name"]: i for i in payload["inventory"]}
    assert by_name["qwen3:4b"]["loaded"] is True
    assert by_name["gemma3:4b"]["loaded"] is False
    assert by_name["qwen3:4b"]["sha256"] == WEIGHTS_SHA


@pytest.mark.asyncio
async def test_heartbeat_inventory_failure_degrades_to_empty(monkeypatch):
    monkeypatch.setattr(
        "daemon.heartbeat.get_gpu_utilization",
        lambda: {"utilization": 0.0, "memory_used_gb": 0.0, "memory_total_gb": 24.0},
    )

    async def broken():
        raise RuntimeError("runtime down")

    manager = HeartbeatManager(client=None, worker_id="w1", get_inventory=broken)
    payload = await manager._build_payload()
    assert payload["inventory"] == []
