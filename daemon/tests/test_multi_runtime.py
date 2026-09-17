"""
Multi-runtime worker daemon: one process driving several runtimes
(e.g. vLLM + Ollama) on a single worker.

Covers the config knob (list-valued, all three layers), the executor
factory, model-to-runtime routing, the unioned heartbeat views, startup
readiness, and per-bundle registration payloads.
"""

import asyncio
from unittest.mock import patch

import pytest

from daemon.config import DaemonConfig
from daemon.executors.base import BaseExecutor
from daemon.executors.ollama import OllamaExecutor
from daemon.executors.vllm import VLLMExecutor
from daemon.executor_factory import create_executors
from daemon.main import _build_parser, _collect_runtime_bundles
from daemon.models import (
    CompletionResult,
    Job,
    PromptRequest,
    WorkerInfo,
    WorkerRuntimeBundle,
)
from daemon.worker import Worker

# -- Fakes -------------------------------------------------------------


class FakeExecutor(BaseExecutor):
    """A runtime that serves a fixed set of names, optionally with
    digests (Ollama-like) and/or an explicit inventory."""

    def __init__(self, runtime, names, healthy=True, detailed=True, inventory=None):
        self.runtime_name = runtime
        self._names = list(names)
        self._healthy = healthy
        self._inventory = inventory if inventory is not None else [
            {"local_name": n, "sha256": None, "size_bytes": None} for n in names
        ]
        self._digests = {n: f"digest-{n}" for n in names}
        self.calls = []
        self.closed = False
        if detailed:
            async def list_models_detailed():
                return [
                    {"name": n, "digest": self._digests.get(n)} for n in self._names
                ]
            self.list_models_detailed = list_models_detailed

    def set_names(self, names):
        self._names = list(names)

    async def execute(self, prompt: PromptRequest) -> CompletionResult:
        self.calls.append((self.runtime_name, prompt.custom_id))
        return CompletionResult(
            custom_id=prompt.custom_id,
            response={"choices": [{"message": {"content": f"{self.runtime_name} ok"}}]},
        )

    async def health_check(self) -> bool:
        return self._healthy

    async def list_models(self):
        return list(self._names)

    async def inventory(self):
        return self.tag_inventory([dict(i) for i in self._inventory])

    async def close(self):
        self.closed = True


class BareExecutor(BaseExecutor):
    """No list_models / list_models_detailed -- the static-config fallback."""

    runtime_name = "bare"

    async def execute(self, prompt: PromptRequest) -> CompletionResult:
        return CompletionResult(custom_id=prompt.custom_id, response={})

    async def health_check(self) -> bool:
        return True


class MockClient:
    def __init__(self):
        self.progress_calls = []
        self.failure_calls = []

    async def report_progress(self, job_id, completed, failed, total):
        self.progress_calls.append((job_id, completed, failed, total))

    async def report_failure(self, job_id, reason):
        self.failure_calls.append((job_id, reason))


def _worker(executors, tmp_path, **cfg):
    config = DaemonConfig(work_dir=str(tmp_path / "jobs"), **cfg)
    worker = Worker(config, MockClient(), executors)
    worker._running = True
    return worker


def _prompts(count=2, model="m"):
    return [
        PromptRequest(
            custom_id=f"p-{i}",
            body={"model": model, "messages": [{"role": "user", "content": "hi"}]},
        )
        for i in range(count)
    ]


# -- Config: list-valued runtime, all three layers ---------------------


class TestRuntimeConfig:
    def test_default_is_ollama(self):
        assert DaemonConfig().runtime == ["ollama"]

    def test_scalar_string_normalized(self):
        assert DaemonConfig(runtime="vllm").runtime == ["vllm"]

    def test_comma_string_normalized(self):
        assert DaemonConfig(runtime="vllm, ollama").runtime == ["vllm", "ollama"]

    def test_list_stripped_deduped_order_kept(self):
        assert DaemonConfig(runtime=[" ollama ", "vllm", "ollama", ""]).runtime == [
            "ollama", "vllm",
        ]

    def test_env_comma_split(self, monkeypatch):
        monkeypatch.setenv("DAEMON_RUNTIME", " vllm , ollama ,")
        assert DaemonConfig.from_env().runtime == ["vllm", "ollama"]

    def test_cli_multiple(self):
        args = _build_parser().parse_args(["--runtime", "vllm", "ollama"])
        assert args.runtime == ["vllm", "ollama"]

        args = _build_parser().parse_args(["--runtime", "vllm"])
        assert args.runtime == ["vllm"]

    def test_env_overrides_yaml(self, monkeypatch, tmp_path):
        path = tmp_path / "cfg.yaml"
        path.write_text("runtime: vllm\n")
        monkeypatch.setenv("DAEMON_RUNTIME", "ollama,vllm")
        assert DaemonConfig.load(config_path=str(path)).runtime == ["ollama", "vllm"]

    def test_unknown_runtime_rejected(self):
        with pytest.raises(ValueError, match="unknown runtime"):
            DaemonConfig(runtime="sparks")

    def test_empty_runtime_rejected(self):
        with pytest.raises(ValueError, match="empty"):
            DaemonConfig(runtime="   ")
        with pytest.raises(ValueError, match="empty"):
            DaemonConfig(runtime=[])


# -- Factory: one executor per runtime ---------------------------------


class TestCreateExecutors:
    def test_single_runtime(self):
        config = DaemonConfig(runtime=["ollama"])
        executors = create_executors(config)
        assert list(executors) == ["ollama"]
        assert isinstance(executors["ollama"], OllamaExecutor)

    def test_mixed_node(self):
        config = DaemonConfig(runtime=["vllm", "ollama"])
        executors = create_executors(config)
        assert list(executors) == ["vllm", "ollama"]
        assert isinstance(executors["vllm"], VLLMExecutor)
        assert isinstance(executors["ollama"], OllamaExecutor)

    def test_vllm_supported_models_single_runtime(self):
        config = DaemonConfig(runtime=["vllm"], models=["llama3:8b"])
        executor = create_executors(config)["vllm"]
        assert executor._supported_models == {"llama3:8b"}

    def test_vllm_supported_models_skipped_mixed(self):
        # The flat config.models list is ambiguous on a mixed node --
        # a name may be served by the other runtime, so the "expected
        # model served" health phase must not run against it.
        config = DaemonConfig(runtime=["vllm", "ollama"], models=["llama3:8b"])
        executor = create_executors(config)["vllm"]
        assert executor._supported_models == set()


# -- vLLM executor: list_models ----------------------------------------


class TestVLLMListModels:
    @staticmethod
    def _fake_v1_models(data):
        import httpx

        class _Resp:
            status_code = 200
            def raise_for_status(self):
                pass
            def json(self):
                return {"data": data}

        async def _get(url, **kw):
            return _Resp()

        client = httpx.AsyncClient()
        client.get = _get
        return client

    @pytest.mark.asyncio
    async def test_served_id_and_root(self):
        ex = VLLMExecutor(base_url="http://x")
        ex._client = self._fake_v1_models([
            {"id": "my-model", "root": "Qwen/Qwen2.5-7B-Instruct"},
            {"id": "alias", "root": "Qwen/Qwen2.5-7B-Instruct"},
            {"id": "adapter", "root": "x/y", "parent": "my-model"},
        ])
        names = await ex.list_models()
        assert names == ["my-model", "Qwen/Qwen2.5-7B-Instruct", "alias"]

    @pytest.mark.asyncio
    async def test_down_server_returns_empty(self):
        ex = VLLMExecutor(base_url="http://127.0.0.1:1")  # nothing there
        assert await ex.list_models() == []


# -- Worker: model to runtime routing ----------------------------------


class TestModelRouting:
    @pytest.mark.asyncio
    async def test_single_executor_routes_everything(self, tmp_path):
        ex = FakeExecutor("ollama", ["a"])
        worker = _worker({"ollama": ex}, tmp_path)
        assert await worker._executor_for("a") is ex
        assert await worker._executor_for("anything-else") is ex
        # Legacy jobs carry no model -- still the one executor.
        assert await worker._executor_for("") is ex

    @pytest.mark.asyncio
    async def test_mixed_worker_routes_by_map(self, tmp_path):
        ollama = FakeExecutor("ollama", ["llama3:8b", "qwen3:4b"])
        vllm = FakeExecutor("vllm", ["my-model", "Qwen/Qwen2.5-7B-Instruct"])
        worker = _worker({"vllm": vllm, "ollama": ollama}, tmp_path)
        await worker._refresh_model_map()

        assert await worker._executor_for("llama3:8b") is ollama
        assert await worker._executor_for("qwen3:4b") is ollama
        assert await worker._executor_for("my-model") is vllm
        assert await worker._executor_for("Qwen/Qwen2.5-7B-Instruct") is vllm
        assert await worker._executor_for("unknown-model") is None
        assert await worker._executor_for("") is None

    @pytest.mark.asyncio
    async def test_stale_map_refreshed_on_miss(self, tmp_path):
        ollama = FakeExecutor("ollama", ["a"])
        vllm = FakeExecutor("vllm", ["b"])
        worker = _worker({"vllm": vllm, "ollama": ollama}, tmp_path)
        await worker._refresh_model_map()

        # A model pulled on the box after registration is not in the
        # map -- the miss triggers one refresh, which finds it.
        vllm.set_names(["b", "c"])
        assert await worker._executor_for("c") is vllm

    @pytest.mark.asyncio
    async def test_conflicting_names_route_to_first(self, tmp_path):
        ollama = FakeExecutor("ollama", ["dup"])
        vllm = FakeExecutor("vllm", ["dup"])
        worker = _worker({"vllm": vllm, "ollama": ollama}, tmp_path)
        await worker._refresh_model_map()
        assert await worker._executor_for("dup") is vllm
        # A refresh must not flip the decision (first-writer-wins).
        await worker._refresh_model_map()
        assert await worker._executor_for("dup") is vllm

    @pytest.mark.asyncio
    async def test_prompts_run_on_routed_executor(self, tmp_path):
        ollama = FakeExecutor("ollama", ["a"])
        vllm = FakeExecutor("vllm", ["b"])
        worker = _worker({"vllm": vllm, "ollama": ollama}, tmp_path)
        await worker._refresh_model_map()

        job = Job(job_id="j1", model="b")
        results = await worker._run_prompts(_prompts(2, model="b"), job)
        assert all(r.is_success for r in results)
        assert [c[0] for c in vllm.calls] == ["vllm", "vllm"]
        assert ollama.calls == []

    @pytest.mark.asyncio
    async def test_unknown_model_fails_every_row(self, tmp_path):
        ollama = FakeExecutor("ollama", ["a"])
        vllm = FakeExecutor("vllm", ["b"])
        worker = _worker({"vllm": vllm, "ollama": ollama}, tmp_path)
        await worker._refresh_model_map()

        job = Job(job_id="j1", model="ghost")
        results = await worker._run_prompts(_prompts(2, model="ghost"), job)
        assert len(results) == 2
        assert all(not r.is_success for r in results)
        assert all("NO_RUNTIME" in r.error for r in results)
        assert ollama.calls == [] and vllm.calls == []

    @pytest.mark.asyncio
    async def test_execute_job_reports_failure_for_unhosted_model(self, tmp_path):
        ollama = FakeExecutor("ollama", ["a"])
        vllm = FakeExecutor("vllm", ["b"])
        worker = _worker({"vllm": vllm, "ollama": ollama}, tmp_path)
        await worker._refresh_model_map()

        job = Job(job_id="j1", model="ghost", input_path="/x")
        await worker._execute_job(job)

        assert len(worker._client.failure_calls) == 1
        assert "ghost" in worker._client.failure_calls[0][1]
        assert ollama.calls == [] and vllm.calls == []


# -- Heartbeat views: union, per-runtime loaded scope ------------------


class TestHeartbeatViews:
    @pytest.mark.asyncio
    async def test_single_executor_loaded_models_unchanged(self, tmp_path):
        ex = FakeExecutor("ollama", ["a", "b"])
        worker = _worker({"ollama": ex}, tmp_path)
        assert await worker._get_loaded_models() == ["a", "b"]

    @pytest.mark.asyncio
    async def test_bare_executor_falls_back_to_config(self, tmp_path):
        worker = _worker({"bare": BareExecutor()}, tmp_path, models=["static"])
        assert await worker._get_loaded_models() == ["static"]

    @pytest.mark.asyncio
    async def test_mixed_worker_unions_across_runtimes(self, tmp_path):
        ollama = FakeExecutor("ollama", ["a", "x"])
        vllm = FakeExecutor("vllm", ["b", "x"])
        worker = _worker({"vllm": vllm, "ollama": ollama}, tmp_path)
        assert await worker._get_loaded_models() == ["b", "x", "a"]

    @pytest.mark.asyncio
    async def test_inventory_union_tagged_and_loaded_scoped(self, tmp_path):
        ollama = FakeExecutor(
            "ollama", ["a"],
            inventory=[{"local_name": "a", "sha256": "a" * 64, "size_bytes": 1}],
        )
        # vLLM holds model "a" on disk too, but has NOT loaded it --
        # only ollama has it in its model list.
        vllm = FakeExecutor(
            "vllm", ["b"],
            inventory=[
                {"local_name": "b", "sha256": "b" * 64, "size_bytes": 1},
                {"local_name": "a", "sha256": "a" * 64, "size_bytes": 1},
            ],
        )
        worker = _worker({"vllm": vllm, "ollama": ollama}, tmp_path)
        items = await worker._get_inventory()

        by_name = {}
        for item in items:
            by_name.setdefault(item["local_name"], []).append(item)

        assert by_name["a"][0]["runtime"] == "vllm"
        assert by_name["a"][0]["loaded"] is False   # not loaded on vllm
        assert by_name["a"][1]["runtime"] == "ollama"
        assert by_name["a"][1]["loaded"] is True    # loaded on ollama
        assert by_name["b"][0]["runtime"] == "vllm"
        assert by_name["b"][0]["loaded"] is True

    @pytest.mark.asyncio
    async def test_heartbeat_preserves_per_item_loaded(self, monkeypatch):
        from daemon.heartbeat import HeartbeatManager

        monkeypatch.setattr(
            "daemon.heartbeat.get_gpu_utilization",
            lambda: {"utilization": 0.0, "memory_used_gb": 0.0, "memory_total_gb": 0.0},
        )
        items = [
            {"local_name": "a", "runtime": "ollama", "loaded": True},
            {"local_name": "b", "runtime": "vllm", "loaded": False},
            {"local_name": "c", "runtime": "vllm"},  # no flag -> union applies
        ]

        async def get_loaded():
            return ["a", "c"]

        async def get_inventory():
            return items

        manager = HeartbeatManager(
            client=None, worker_id="w1",
            get_loaded_models=get_loaded, get_inventory=get_inventory,
        )
        payload = await manager._build_payload()
        inv = {i["local_name"]: i["loaded"] for i in payload["inventory"]}
        assert inv == {"a": True, "b": False, "c": True}


# -- Startup readiness -------------------------------------------------


@pytest.fixture(autouse=True)
def _fast_readiness(monkeypatch):
    import daemon.worker as worker_mod

    monkeypatch.setattr(worker_mod, "READINESS_RETRY_DELAY", 0.01)


class TestReadiness:
    @pytest.mark.asyncio
    async def test_all_ready(self, tmp_path):
        ollama = FakeExecutor("ollama", ["a"])
        vllm = FakeExecutor("vllm", ["b"])
        worker = _worker({"vllm": vllm, "ollama": ollama}, tmp_path)
        ready = await worker.wait_for_runtimes()
        assert ready == ["vllm", "ollama"]

    @pytest.mark.asyncio
    async def test_partial_readiness(self, tmp_path):
        ollama = FakeExecutor("ollama", ["a"], healthy=True)
        vllm = FakeExecutor("vllm", ["b"], healthy=False)
        worker = _worker({"vllm": vllm, "ollama": ollama}, tmp_path)
        ready = await worker.wait_for_runtimes()
        assert ready == ["ollama"]

    @pytest.mark.asyncio
    async def test_none_ready(self, tmp_path):
        worker = _worker({"vllm": FakeExecutor("vllm", [], healthy=False)}, tmp_path)
        ready = await worker.wait_for_runtimes()
        assert ready == []

    @pytest.mark.asyncio
    async def test_map_built_before_first_poll(self, tmp_path):
        ollama = FakeExecutor("ollama", ["a"])
        vllm = FakeExecutor("vllm", ["b"])
        worker = _worker({"vllm": vllm, "ollama": ollama}, tmp_path)
        await worker.wait_for_runtimes()
        assert worker._model_runtimes == {"a": "ollama", "b": "vllm"}


# -- Registration bundles ----------------------------------------------


class TestRuntimeBundles:
    @pytest.mark.asyncio
    async def test_partial_readiness_drops_down_runtime(self):
        ollama = FakeExecutor("ollama", ["a"])
        vllm = FakeExecutor("vllm", ["b"])
        config = DaemonConfig(runtime=["vllm", "ollama"], models=["static"])
        bundles = await _collect_runtime_bundles(
            config, {"vllm": vllm, "ollama": ollama}, ["ollama"],
        )
        assert [b.runtime for b in bundles] == ["ollama"]
        # Mixed node: bundle models are the dynamic names, not the
        # ambiguous static list.
        assert bundles[0].models == ["a"]
        assert bundles[0].model_digests == {"a": "digest-a"}

    @pytest.mark.asyncio
    async def test_none_ready_advertises_everything(self):
        ollama = FakeExecutor("ollama", ["a"])
        vllm = FakeExecutor("vllm", ["b"])
        config = DaemonConfig(runtime=["vllm", "ollama"])
        bundles = await _collect_runtime_bundles(
            config, {"vllm": vllm, "ollama": ollama}, [],
        )
        assert [b.runtime for b in bundles] == ["vllm", "ollama"]

    @pytest.mark.asyncio
    async def test_single_runtime_merges_static_and_dynamic(self):
        ollama = FakeExecutor("ollama", ["dyn"])
        config = DaemonConfig(runtime=["ollama"], models=["static"])
        bundles = await _collect_runtime_bundles(
            config, {"ollama": ollama}, ["ollama"],
        )
        assert bundles[0].models == ["static", "dyn"]
        assert bundles[0].runtime == "ollama"

    @pytest.mark.asyncio
    async def test_inventory_carried_per_bundle(self):
        # vllm fake without digests (like the real VLLMExecutor): its
        # bundle names come from nowhere static -- just inventory.
        ollama = FakeExecutor("ollama", ["a"], detailed=False, inventory=[
            {"local_name": "a", "sha256": "a" * 64, "size_bytes": 9},
        ])
        vllm = FakeExecutor("vllm", ["b"], detailed=False, inventory=[
            {"local_name": "b", "sha256": "b" * 64, "size_bytes": 8},
        ])
        config = DaemonConfig(runtime=["vllm", "ollama"])
        bundles = await _collect_runtime_bundles(
            config, {"vllm": vllm, "ollama": ollama}, ["vllm", "ollama"],
        )
        by_runtime = {b.runtime: b for b in bundles}
        assert by_runtime["ollama"].models == []
        assert by_runtime["ollama"].inventory[0]["sha256"] == "a" * 64
        assert by_runtime["ollama"].inventory[0]["runtime"] == "ollama"
        assert by_runtime["vllm"].inventory[0]["sha256"] == "b" * 64


# -- Registration wire payload ------------------------------------------


class TestRegisterPayload:
    def test_one_entry_per_runtime_bundle(self):
        from daemon.client import BackendClient

        client = BackendClient(base_url="http://x", worker_id="w", api_key="k")
        info = WorkerInfo(
            worker_id="w",
            runtimes=[
                WorkerRuntimeBundle(
                    runtime="vllm",
                    models=["m1"],
                    model_digests={"m1": "d1"},
                    inventory=[
                        {"local_name": "m1", "sha256": "a" * 64,
                         "size_bytes": 1, "runtime": "vllm"},
                    ],
                ),
                WorkerRuntimeBundle(runtime="ollama", models=["m2"],
                                    model_digests={"m2": "d2"}),
            ],
        )
        captured = {}

        async def fake_post(url, json=None, **kw):
            captured["url"] = url
            captured["json"] = json

            class _R:
                status_code = 200

                def raise_for_status(self):
                    pass

                def json(self):
                    return {"worker_id": "w"}

            return _R()

        with patch.object(
            client, "_get_client",
            lambda: type("C", (), {"post": staticmethod(fake_post)})(),
        ):
            asyncio.run(client.register_worker(info))

        assert captured["url"] == "/workers/register"
        entries = captured["json"]["runtimes"]
        assert [e["type"] for e in entries] == ["vllm", "ollama"]
        assert entries[0]["models"] == ["m1"]
        assert entries[0]["model_digests"] == {"m1": "d1"}
        assert entries[0]["inventory"][0]["sha256"] == "a" * 64
        assert entries[1]["inventory"] == []
