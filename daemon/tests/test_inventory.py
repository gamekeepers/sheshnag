"""On-disk inventory reporting (#116): manifest-layer file hashes, name
derivation, the /api/tags fallback, and the heartbeat payload."""
import json
import os
import time

import httpx
import pytest

from daemon import hf_cache
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


def _empty_hub(tmp_path):
    """A hub cache holding nothing — `resolve_hub_cache` skips a path that does
    not exist and would otherwise reach this machine's real cache."""
    hub = tmp_path / "empty-hub"
    hub.mkdir(exist_ok=True)
    return hub

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
        "runtime": "ollama",
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
    # Auto-detection must not pick up the real machine's Ollama store.
    monkeypatch.setattr(ex, "_resolve_models_dir", lambda: None)
    # /api/tags digest deliberately present — it must NOT leak into sha256.
    ex._client = _api_client({"qwen3:4b": {"digest": "d" * 64, "quantization_level": "Q4_K_M",
                                            "parameter_size": "4.0B", "family": "qwen3",
                                            "context_length": 40960}})
    items = await ex.inventory()
    assert items == [{"local_name": "qwen3:4b", "sha256": None, "size_bytes": None,
                      "details": {"quantization": "Q4_K_M", "parameter_size": "4.0B",
                                  "family": "qwen3", "context_length": 40960},
                      "runtime": "ollama"}]


def _async(value):
    async def _coro():
        return value
    return _coro()


def _api_client(models, show_calls=None, calls=None, timeout=False):
    """Mock Ollama API: /api/tags with `details`, /api/show with model_info.
    `models` = {name: {"quantization_level", "parameter_size", "family",
    "context_length", optional "digest"}}. `show_calls` records /api/show
    names; `calls` records every request path; `timeout=True` makes every
    request raise like a black-holed endpoint."""
    def handler(request):
        if calls is not None:
            calls.append(request.url.path)
        if timeout:
            raise httpx.ConnectTimeout("blackhole", request=request)
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [
                {"name": n, "digest": d.get("digest", "x" * 64),
                 "details": {k: v for k, v in d.items() if k not in ("context_length", "digest")}}
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
async def test_vllm_inventory_reports_served_name_and_root(tmp_path):
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

    ex = VLLMExecutor(base_url="http://vllm.test", hf_hub_cache=str(_empty_hub(tmp_path)))
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


@pytest.mark.asyncio
async def test_details_steady_state_makes_zero_api_calls(tmp_path):
    _write_manifest(tmp_path, "registry.ollama.ai", "library", "qwen3", "4b",
                    [_model_layer(WEIGHTS_SHA, 10)])
    calls = []
    ex = OllamaExecutor(models_dir=str(tmp_path))
    ex._client = _api_client({"qwen3:4b": {"quantization_level": "Q4_K_M", "parameter_size": "4.0B",
                                            "family": "qwen3", "context_length": 1}}, calls=calls)
    await ex.inventory()
    assert calls == ["/api/tags", "/api/show"]
    await ex.inventory()
    await ex.inventory()
    assert calls == ["/api/tags", "/api/show"]     # cached: the manifest path is filesystem-only again


@pytest.mark.asyncio
async def test_details_api_failure_is_negative_cached(tmp_path):
    """A black-holed Ollama endpoint must not cost 10s x (N+1) on EVERY beat:
    one failed /api/tags backs the daemon off for DETAILS_RETRY_SECONDS, and
    the inventory (hashes!) is still reported with details=None."""
    for i in range(3):
        _write_manifest(tmp_path, "registry.ollama.ai", "library", f"m{i}", "1b",
                        [_model_layer(str(i) * 64, 10)])
    calls = []
    ex = OllamaExecutor(models_dir=str(tmp_path))
    ex._client = _api_client({}, calls=calls, timeout=True)
    items = await ex.inventory()
    assert len(items) == 3 and all(i["details"] is None for i in items)
    assert all(i["sha256"] for i in items)
    await ex.inventory()
    await ex.inventory()
    assert calls == ["/api/tags"]                   # one attempt, then backed off
    # Window elapsed -> exactly one more attempt.
    ex._details_api_failed_at -= ex.DETAILS_RETRY_SECONDS + 1
    await ex.inventory()
    assert calls == ["/api/tags", "/api/tags"]


@pytest.mark.asyncio
async def test_details_lookups_capped_per_beat(tmp_path):
    """N models converge over ceil(N/cap) beats instead of one beat paying
    N x show-timeout on a slow server."""
    models = {}
    for i in range(6):
        _write_manifest(tmp_path, "registry.ollama.ai", "library", f"cap{i}", "1b",
                        [_model_layer(chr(ord("a") + i) * 64, 10)])
        models[f"cap{i}:1b"] = {"quantization_level": "Q4_0", "parameter_size": "1B",
                                "family": "x", "context_length": 100 + i}
    show_calls = []
    ex = OllamaExecutor(models_dir=str(tmp_path))
    ex._client = _api_client(models, show_calls)
    items = await ex.inventory()
    assert len(show_calls) == ex.DETAILS_LOOKUPS_PER_BEAT
    assert sum(i["details"] is not None for i in items) == ex.DETAILS_LOOKUPS_PER_BEAT
    items = await ex.inventory()
    assert len(show_calls) == 6 and all(i["details"] for i in items)
    await ex.inventory()
    assert len(show_calls) == 6


@pytest.mark.asyncio
async def test_details_unknown_to_api_retried_only_after_window(tmp_path):
    """On-disk model the API does not know (desync): not a /api/show per beat."""
    _write_manifest(tmp_path, "registry.ollama.ai", "library", "ghost", "1b",
                    [_model_layer(MMPROJ_SHA, 10)])
    show_calls = []
    ex = OllamaExecutor(models_dir=str(tmp_path))
    ex._client = _api_client({"other:1b": {"quantization_level": "Q4_0", "parameter_size": "1B",
                                            "family": "x", "context_length": 1}}, show_calls)
    await ex.inventory()
    await ex.inventory()
    assert show_calls == ["ghost:1b"]


@pytest.mark.asyncio
async def test_details_no_hash_path_keyed_by_artifact_not_name(tmp_path, monkeypatch):
    """Fallback path: re-pulling `qwen3:4b` with different weights changes
    /api/tags' digest, so cached details must NOT survive under the name."""
    ex = OllamaExecutor(models_dir=str(tmp_path / "nonexistent"))
    monkeypatch.setattr(ex, "_resolve_models_dir", lambda: None)
    first = {"qwen3:4b": {"digest": "1" * 64, "quantization_level": "Q4_K_M",
                          "parameter_size": "4.0B", "family": "qwen3", "context_length": 4096}}
    ex._client = _api_client(first)
    assert (await ex.inventory())[0]["details"]["context_length"] == 4096
    repulled = {"qwen3:4b": {"digest": "2" * 64, "quantization_level": "Q8_0",
                             "parameter_size": "4.0B", "family": "qwen3", "context_length": 40960}}
    ex._client = _api_client(repulled)
    item = (await ex.inventory())[0]
    assert item["details"]["quantization"] == "Q8_0"
    assert item["details"]["context_length"] == 40960


# ─── vLLM identity from the HF hub cache ─────────────────────

SHARD1 = "1" * 64
SHARD2 = "2" * 64
REV = "0a1b2c3d4e5f60718293a4b5c6d7e8f9a0b1c2d3"


def _hub_cache(tmp_path, repo="Org/Name", quantized=False):
    """models--Org--Name/{refs/main, blobs/<sha>, snapshots/<rev>/...} exactly
    as huggingface_hub lays it out: weight files are symlinks into blobs/."""
    hub = tmp_path / "hub"
    repo_dir = hub / ("models--" + repo.replace("/", "--"))
    (repo_dir / "refs").mkdir(parents=True)
    (repo_dir / "refs" / "main").write_text(REV + "\n")
    blobs = repo_dir / "blobs"
    blobs.mkdir()
    (blobs / SHARD1).write_bytes(b"x" * 1000)
    (blobs / SHARD2).write_bytes(b"y" * 500)
    snap = repo_dir / "snapshots" / REV
    snap.mkdir(parents=True)
    (snap / "model-00001-of-00002.safetensors").symlink_to(f"../../blobs/{SHARD1}")
    (snap / "model-00002-of-00002.safetensors").symlink_to(f"../../blobs/{SHARD2}")
    config = {"model_type": "llama", "torch_dtype": "bfloat16", "max_position_embeddings": 8192}
    if quantized:
        config["quantization_config"] = {"quant_method": "awq", "bits": 4}
    (snap / "config.json").write_text(json.dumps(config))
    (snap / "model.safetensors.index.json").write_text(json.dumps(
        {"metadata": {"total_size": 2_400_000_000}}))
    return hub, snap


def _vllm_client(models):
    def handler(request):
        assert request.url.path == "/v1/models"
        return httpx.Response(200, json={"data": models})
    return httpx.AsyncClient(base_url="http://vllm.test", transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_vllm_identity_from_hub_cache(tmp_path):
    """root = HF repo id -> shard hashes from the content-addressed cache,
    details from config.json, repo + commit as the pull reference; the
    served alias row carries the same identity (same artifact, two names)."""
    hub, _ = _hub_cache(tmp_path)
    ex = VLLMExecutor(base_url="http://vllm.test", hf_hub_cache=str(hub))
    ex._client = _vllm_client([{"id": "served-alias", "root": "Org/Name"}])
    items = {i["local_name"]: i for i in await ex.inventory()}
    assert set(items) == {"served-alias", "Org/Name"}
    for item in items.values():
        assert item["sha256"] == SHARD1 and item["size_bytes"] == 1500  # total, not shard 1
        assert [(f["file"], f["sha256"], f["size_bytes"]) for f in item["files"]] == [
            ("model-00001-of-00002.safetensors", SHARD1, 1000),
            ("model-00002-of-00002.safetensors", SHARD2, 500),
        ]
        assert item["details"] == {
            "quantization": "bf16", "parameter_size": "1.2B", "family": "llama",
            "context_length": 8192, "source_ref": "Org/Name", "source_revision": REV,
        }


@pytest.mark.asyncio
async def test_vllm_identity_from_snapshot_path_and_quantized(tmp_path):
    """root as a filesystem path inside the cache maps back to its repo;
    a quantization_config wins over torch_dtype and params are not guessed."""
    hub, snap = _hub_cache(tmp_path, quantized=True)
    ex = VLLMExecutor(base_url="http://vllm.test", hf_hub_cache=str(hub))
    ex._client = _vllm_client([{"id": str(snap), "root": str(snap)}])
    item = (await ex.inventory())[0]
    assert item["sha256"] == SHARD1
    assert item["details"]["quantization"] == "awq-4bit"
    assert item["details"]["parameter_size"] is None
    assert (item["details"]["source_ref"], item["details"]["source_revision"]) == ("Org/Name", REV)


@pytest.mark.asyncio
async def test_vllm_uncached_model_stays_hashless(tmp_path):
    hub, _ = _hub_cache(tmp_path)
    ex = VLLMExecutor(base_url="http://vllm.test", hf_hub_cache=str(hub))
    ex._client = _vllm_client([{"id": "Other/NotCached", "root": "Other/NotCached"},
                               {"id": "local", "root": "/opt/models/local"}])
    items = await ex.inventory()

    # The served rows: neither name is in the cache, so neither gets an identity.
    served = [i for i in items if i.get("loaded")]
    assert {i["local_name"] for i in served} == {
        "Other/NotCached", "local", "/opt/models/local"}
    for item in served:
        assert item["sha256"] is None and not item.get("files")

    # What the cache holds is reported too, as held-not-loaded — a model can be
    # catalogued without this server ever having served it.
    held = [i for i in items if not i.get("loaded")]
    assert [i["local_name"] for i in held] == ["Org/Name"]
    assert held[0]["files"], "a held row carries the shard hashes that confirm it"


# ─── vLLM: LoRA adapters ─────────────────────────────────────

@pytest.mark.asyncio
async def test_vllm_inventory_skips_lora_adapters(tmp_path):
    """/v1/models lists every --enable-lora adapter as a ModelCard with
    `parent` set; an adapter is not a standalone model, so it is never
    inventoried (its PEFT weights would otherwise get the base model's
    hash and be adopted as a chat model)."""
    def handler(request):
        assert request.url.path == "/v1/models"
        return httpx.Response(200, json={"data": [
            {"id": "Qwen/Qwen2.5-7B-Instruct", "root": "Qwen/Qwen2.5-7B-Instruct"},
            {"id": "finance-lora",
             "root": "/root/.cache/huggingface/hub/models--acme--finance-lora/snapshots/c0ffe12",
             "parent": "Qwen/Qwen2.5-7B-Instruct"},
        ]})

    ex = VLLMExecutor(base_url="http://vllm.test", hf_hub_cache=str(_empty_hub(tmp_path)))
    ex._client = httpx.AsyncClient(
        base_url="http://vllm.test", transport=httpx.MockTransport(handler))
    items = await ex.inventory()
    assert [i["local_name"] for i in items] == ["Qwen/Qwen2.5-7B-Instruct"]


def _adapter_cache(tmp_path, repo="acme/finance-lora"):
    """A cached PEFT adapter: adapter_config.json + adapter weights, no
    base-model config/index."""
    hub = tmp_path / "hub"
    repo_dir = hub / ("models--" + repo.replace("/", "--"))
    (repo_dir / "refs").mkdir(parents=True)
    (repo_dir / "refs" / "main").write_text(REV + "\n")
    blobs = repo_dir / "blobs"
    blobs.mkdir()
    (blobs / SHARD1).write_bytes(b"l" * 100)
    snap = repo_dir / "snapshots" / REV
    snap.mkdir(parents=True)
    (snap / "adapter_config.json").write_text(json.dumps(
        {"base_model_name_or_path": "Qwen/Qwen2.5-7B-Instruct"}))
    (snap / "adapter_model.safetensors").symlink_to(f"../../blobs/{SHARD1}")
    return hub, snap


@pytest.mark.asyncio
async def test_vllm_standalone_adapter_reports_no_identity(tmp_path):
    """Defense in depth: an adapter snapshot served on its own (no `parent`
    in the card) still has no base-model identity — describe() refuses to
    read adapter weights as model shards, so the row stays hash-less."""
    hub, _ = _adapter_cache(tmp_path)
    ex = VLLMExecutor(base_url="http://vllm.test", hf_hub_cache=str(hub))
    ex._client = _vllm_client([{"id": "acme/finance-lora", "root": "acme/finance-lora"}])
    items = await ex.inventory()
    assert len(items) == 1
    item = items[0]
    assert item["sha256"] is None and item["files"] is None
    assert item["details"].get("family") is None
    assert item["details"]["source_ref"] == "acme/finance-lora"


# ─── vLLM: local paths, dangling blobs, identity cache ───────

def test_local_dir_named_like_repo_id_is_never_public(tmp_path):
    """The daemon's CWD holds ./Qwen/Qwen2.5-7B-Instruct/ — a local AWQ
    quant vLLM loads by path. Before the fix its name matched _REPO_ID, so
    identity was read from the PUBLIC cache snapshot: the public bf16's
    hashes adopted for different bytes. A real directory is a local
    checkout — no public identity — unless it IS a cache snapshot."""
    hub, _ = _hub_cache(tmp_path)
    local = tmp_path / "Qwen" / "Qwen2.5-7B-Instruct"
    local.mkdir(parents=True)
    assert hf_cache.locate(hub, str(local)) is None
    # A bare repo id (not a directory) still resolves through the cache.
    assert hf_cache.locate(hub, "Org/Name") is not None
    # ...and a repo-id-named dir that IS a cache snapshot still maps back.
    hub2, snap2 = _hub_cache(tmp_path / "h2")
    assert hf_cache.locate(hub2, str(snap2)) is not None


@pytest.mark.asyncio
async def test_dangling_blobs_carry_no_identity(tmp_path):
    """After `hf cache gc` the symlinks survive but the blobs are gone.
    _blob_sha256 still reads the link NAME, so describe() lists files with
    a sha but a failed stat; _identify must require the blob to be intact,
    so all-blobs-gone falls back to hash-less (name-matched)."""
    hub, _ = _hub_cache(tmp_path)
    for b in (hub / "models--Org--Name" / "blobs").iterdir():
        b.unlink()
    ex = VLLMExecutor(base_url="http://vllm.test", hf_hub_cache=str(hub))
    ex._client = _vllm_client([{"id": "served-alias", "root": "Org/Name"}])
    for item in await ex.inventory():
        assert item["sha256"] is None and item["files"] is None


@pytest.mark.asyncio
async def test_identity_describe_cached_per_snapshot_mtime(tmp_path, monkeypatch):
    """Steady-state heartbeat: the mtime cache makes the second beat a
    single stat — describe() (all symlinks + config + full index) runs
    exactly once across both beats."""
    hub, _ = _hub_cache(tmp_path)
    ex = VLLMExecutor(base_url="http://vllm.test", hf_hub_cache=str(hub))
    ex._client = _vllm_client([{"id": "served-alias", "root": "Org/Name"}])
    calls, real = [], hf_cache.describe
    monkeypatch.setattr(hf_cache, "describe", lambda s: (calls.append(s), real(s))[1])
    await ex.inventory()
    await ex.inventory()
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_identity_cache_invalidates_on_mtime_change(tmp_path, monkeypatch):
    """A new snapshot landing (files added) bumps the dir mtime, so the
    cache misses and describe() runs again."""
    hub, snap = _hub_cache(tmp_path)
    ex = VLLMExecutor(base_url="http://vllm.test", hf_hub_cache=str(hub))
    ex._client = _vllm_client([{"id": "served-alias", "root": "Org/Name"}])
    calls, real = [], hf_cache.describe
    monkeypatch.setattr(hf_cache, "describe", lambda s: (calls.append(s), real(s))[1])
    await ex.inventory()
    assert len(calls) == 1
    os.utime(snap, (time.time() + 10, time.time() + 10))
    await ex.inventory()
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_inventory_identifies_each_distinct_root_once(tmp_path):
    """Three cards sharing one root (served alias + repo id + another
    alias) identify the snapshot once, and every row carries the same
    identity."""
    hub, _ = _hub_cache(tmp_path)
    ex = VLLMExecutor(base_url="http://vllm.test", hf_hub_cache=str(hub))
    ex._client = _vllm_client([
        {"id": "alias-a", "root": "Org/Name"},
        {"id": "Org/Name", "root": "Org/Name"},
        {"id": "alias-b", "root": "Org/Name"},
    ])
    calls, real = [], ex._identify
    ex._identify = lambda root: (calls.append(root), real(root))[1]
    items = await ex.inventory()
    assert calls == ["Org/Name"]
    assert {i["local_name"] for i in items} == {"alias-a", "Org/Name", "alias-b"}
    assert all(i["sha256"] == SHARD1 for i in items)


# ─── hf_cache.snapshot_for: revision choice ──────────────────

def _repo_with_snapshots(tmp_path, revs, main_ref=None):
    repo = tmp_path / "models--Org--Name"
    for rev in revs:
        (repo / "snapshots" / rev).mkdir(parents=True)
    if main_ref is not None:
        (repo / "refs").mkdir(parents=True, exist_ok=True)
        (repo / "refs" / "main").write_text(main_ref + "\n")
    return repo


def test_snapshot_single_dir_is_unambiguous_without_any_ref(tmp_path):
    """vLLM can only serve what is in its cache: one snapshot dir IS the
    commit, regardless of refs (tags live under refs/tags, commit pulls
    write no ref at all)."""
    repo = _repo_with_snapshots(tmp_path, ["0a1b2c3d"])
    rev, snap = hf_cache.snapshot_for(repo)
    assert rev == "0a1b2c3d" and snap == repo / "snapshots" / "0a1b2c3d"


def test_snapshot_single_dir_ignores_stale_main_ref(tmp_path):
    """refs/main dangling (pull interrupted) must not shadow the only
    snapshot — the box is serving it."""
    repo = _repo_with_snapshots(tmp_path, ["0a1b2c3d"], main_ref="deadbeef")
    assert hf_cache.snapshot_for(repo)[0] == "0a1b2c3d"


def test_snapshot_multiple_dirs_honours_valid_main_ref(tmp_path):
    repo = _repo_with_snapshots(tmp_path, ["0a1b2c3d", "9f9f9f9f"], main_ref="9f9f9f9f")
    assert hf_cache.snapshot_for(repo)[0] == "9f9f9f9f"


def test_snapshot_multiple_dirs_without_ref_refuses_to_guess(tmp_path):
    """--revision v0.5.0 and a later main pull: two dirs, no ref pointing
    at an existing snapshot. Picking by mtime would pin the wrong commit
    and digest-mismatch into permanent quarantine — None instead."""
    repo = _repo_with_snapshots(tmp_path, ["0a1b2c3d", "9f9f9f9f"])
    assert hf_cache.snapshot_for(repo) is None


def test_snapshot_multiple_dirs_with_stale_ref_refuses_to_guess(tmp_path):
    repo = _repo_with_snapshots(tmp_path, ["0a1b2c3d", "9f9f9f9f"], main_ref="deadbeef")
    assert hf_cache.snapshot_for(repo) is None


def test_snapshot_no_dirs_is_none(tmp_path):
    repo = tmp_path / "models--Org--Name"
    (repo / "snapshots").mkdir(parents=True)
    assert hf_cache.snapshot_for(repo) is None
    assert hf_cache.snapshot_for(tmp_path / "missing") is None


# ─── hf_cache._weight_files: identity shard choice ───────────

def _file_snapshot(tmp_path, entries):
    """A snapshot dir; entries is {filename: blob_sha or None} — a sha makes
    the file a symlink into blobs/ (as the hub lays it out), None a plain
    file."""
    repo = tmp_path / "models--Org--Name"
    blobs = repo / "blobs"
    blobs.mkdir(parents=True)
    snap = repo / "snapshots" / REV
    snap.mkdir(parents=True)
    for name, sha in entries.items():
        if sha:
            (blobs / sha).write_bytes(b"z" * (10 + len(name)))
            (snap / name).symlink_to(f"../../blobs/{sha}")
        else:
            (snap / name).write_bytes(b"q" * len(name))
    return snap


def test_weight_selection_follows_the_repo_index(tmp_path):
    """consolidated.safetensors sorts before model-*, but the repo's own
    weight_map says the shards are the weights: identity = shard 1, only
    index-listed shards reported, consolidated ignored."""
    W1, W2, W3 = "a1" * 32, "a2" * 32, "a3" * 32
    snap = _file_snapshot(tmp_path, {
        "consolidated.safetensors": W3,
        "model-00001-of-00002.safetensors": W1,
        "model-00002-of-00002.safetensors": W2,
    })
    (snap / "model.safetensors.index.json").write_text(json.dumps({"weight_map": {
        "model.embed_tokens.weight": "model-00001-of-00002.safetensors",
        "model.layers.0.weight": "model-00001-of-00002.safetensors",
        "lm_head.weight": "model-00002-of-00002.safetensors",
    }}))
    files = hf_cache.describe(snap)["files"]
    assert [f["file"] for f in files] == [
        "model-00001-of-00002.safetensors", "model-00002-of-00002.safetensors"]
    assert files[0]["sha256"] == W1


def test_weight_selection_never_crows_optimizer_bin(tmp_path):
    """Legacy sharded bin: a filename sort puts optimizer.bin (o < p) first
    and would crown the trainer's optimizer state as the model's identity —
    shard NUMBER orders the real weights instead."""
    P1, P2 = "c1" * 32, "c2" * 32
    snap = _file_snapshot(tmp_path, {
        "optimizer.bin": "c0" * 32,
        "pytorch_model-00001-of-00002.bin": P1,
        "pytorch_model-00002-of-00002.bin": P2,
    })
    files = hf_cache.describe(snap)["files"]
    assert [f["file"] for f in files] == [
        "pytorch_model-00001-of-00002.bin", "pytorch_model-00002-of-00002.bin"]
    assert files[0]["sha256"] == P1


def test_weight_selection_single_file_ignores_other_bins(tmp_path):
    M, O = "d1" * 32, "d2" * 32
    snap = _file_snapshot(tmp_path, {"model.safetensors": M, "optimizer.bin": O})
    files = hf_cache.describe(snap)["files"]
    assert [(f["file"], f["sha256"]) for f in files] == [("model.safetensors", M)]
