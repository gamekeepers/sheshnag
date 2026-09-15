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
    items = await ex.inventory()
    assert items == [{
        "local_name": "qwen3:4b",
        "sha256": WEIGHTS_SHA,
        "size_bytes": 2497280256,
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
    ex = OllamaExecutor(models_dir=str(tmp_path))
    names = {i["local_name"] for i in await ex.inventory()}
    # Default-registry library models render bare; other hosts keep their prefix.
    assert names == {"gemma3:4b", "hf.co/unsloth/qwen3-gguf:Q4_K_M"}


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
    items = await ex.inventory()
    assert items == [{"local_name": "qwen3:4b", "sha256": None, "size_bytes": None}]


def _async(value):
    async def _coro():
        return value
    return _coro()


# ─── vLLM ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_vllm_inventory_prefers_root_over_id():
    def handler(request):
        assert request.url.path == "/v1/models"
        return httpx.Response(200, json={"data": [
            {"id": "served-alias", "root": "Qwen/Qwen3-4B-Instruct"},
            {"id": "no-root-model"},
        ]})

    ex = VLLMExecutor(base_url="http://vllm.test")
    ex._client = httpx.AsyncClient(
        base_url="http://vllm.test", transport=httpx.MockTransport(handler),
    )
    items = await ex.inventory()
    assert [i["local_name"] for i in items] == [
        "Qwen/Qwen3-4B-Instruct", "no-root-model",
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
