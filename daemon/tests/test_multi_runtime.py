"""
Multi-runtime worker daemon: one process driving several runtimes
(e.g. vLLM + Ollama) on a single worker.

Covers the config knob (list-valued, all three layers), the executor
factory, model-to-runtime routing, the unioned heartbeat views, startup
readiness, and per-bundle registration payloads.
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from daemon.config import DaemonConfig
from daemon.executors.base import BaseExecutor
from daemon.executors.ollama import OllamaExecutor
from daemon.executors.vllm import VLLMExecutor
from daemon.executor_factory import create_executors
from daemon.executors.llamacpp import LlamaCppExecutor
from daemon.main import _build_cli_overrides, _build_parser, _collect_runtime_bundles
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
    digests (Ollama-like) and/or an explicit inventory.

    `names` is what the runtime can serve; `running` is what it holds in
    VRAM. They default to the same list — a test that cares about the
    difference passes `running` explicitly."""

    def __init__(self, runtime, names, healthy=True, detailed=True, inventory=None,
                 running=None):
        self.runtime_name = runtime
        self._names = list(names)
        self._running = list(names) if running is None else list(running)
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

    async def list_running_models(self):
        return list(self._running)

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


class FakeOllama(OllamaExecutor):
    """OllamaExecutor that fakes pulls: a successful pull adds the model
    to its list, like the real runtime serves what it has pulled."""

    def __init__(self, names, pull_ok=True):
        super().__init__(base_url="http://ollama.test")
        self._names = list(names)
        self._pull_ok = pull_ok
        self.pulled = []

    async def execute(self, prompt: PromptRequest) -> CompletionResult:
        return CompletionResult(
            custom_id=prompt.custom_id,
            response={"choices": [{"message": {"content": "ollama ok"}}]},
        )

    async def health_check(self) -> bool:
        return True

    async def list_models(self):
        return list(self._names)

    async def list_running_models(self):
        return list(self._names)

    async def pull_model(self, model_name, progress_callback=None):
        self.pulled.append(model_name)
        if self._pull_ok:
            self._names.append(model_name)
        return self._pull_ok

    async def close(self):
        pass


class MockClient:
    def __init__(self, input_lines=None):
        self.progress_calls = []
        self.failure_calls = []
        self.upload_calls = []
        self.download_calls = []
        self._input_lines = list(input_lines or [])

    async def report_progress(self, job_id, completed, failed, total):
        self.progress_calls.append((job_id, completed, failed, total))

    async def report_failure(self, job_id, reason):
        self.failure_calls.append((job_id, reason))

    async def download_input(self, job_id, input_path, dest_path):
        self.download_calls.append(job_id)
        dest = Path(dest_path)
        dest.write_text(
            "".join(json.dumps(line) + "\n" for line in self._input_lines)
        )

    async def upload_results(self, job_id, path, completed, failed):
        self.upload_calls.append((job_id, completed, failed))


def _worker(executors, tmp_path, client=None, **cfg):
    config = DaemonConfig(work_dir=str(tmp_path / "jobs"), **cfg)
    worker = Worker(config, client or MockClient(), executors)
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
        # --runtime is nargs="+" with action="append", so the raw parse
        # is one list per flag occurrence; _build_cli_overrides
        # flattens it back to the plain list the config layer takes.
        args = _build_parser().parse_args(["--runtime", "vllm", "ollama"])
        assert args.runtime == [["vllm", "ollama"]]
        assert _build_cli_overrides(args)["runtime"] == ["vllm", "ollama"]

        args = _build_parser().parse_args(["--runtime", "vllm"])
        assert _build_cli_overrides(args)["runtime"] == ["vllm"]

    def test_cli_repeated_runtime_flags(self):
        # The repeated-flag form the help text advertises: every
        # occurrence must survive, in order, instead of the last
        # silently clobbering the rest (the store action used to do
        # exactly that).
        args = _build_parser().parse_args(
            ["--runtime", "ollama", "--runtime", "vllm"],
        )
        assert args.runtime == [["ollama"], ["vllm"]]
        assert _build_cli_overrides(args)["runtime"] == ["ollama", "vllm"]

    def test_env_overrides_yaml(self, monkeypatch, tmp_path):
        path = tmp_path / "cfg.yaml"
        path.write_text("runtime: vllm\n")
        monkeypatch.setenv("DAEMON_RUNTIME", "ollama,vllm")
        assert DaemonConfig.load(config_path=str(path)).runtime == ["ollama", "vllm"]

    def test_unknown_runtime_rejected(self):
        with pytest.raises(ValueError, match="unknown runtime"):
            DaemonConfig(runtime="sparks")

    def test_empty_runtime_rejected(self, monkeypatch):
        with pytest.raises(ValueError, match="empty"):
            DaemonConfig(runtime="   ")
        with pytest.raises(ValueError, match="empty"):
            DaemonConfig(runtime=[])
        # The env layer too: a set-but-empty DAEMON_RUNTIME (e.g. a
        # blanked EnvironmentFile entry) must fail at startup like the
        # other empty forms, not silently drive the default runtime.
        monkeypatch.setenv("DAEMON_RUNTIME", "")
        with pytest.raises(ValueError, match="empty"):
            DaemonConfig.from_env()


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

    def test_llamacpp_is_a_known_runtime(self):
        config = DaemonConfig(runtime=["llamacpp"])
        executors = create_executors(config)
        assert list(executors) == ["llamacpp"]
        assert isinstance(executors["llamacpp"], LlamaCppExecutor)

    def test_llamacpp_url_reaches_the_executor(self):
        config = DaemonConfig(
            runtime=["llamacpp"], llamacpp_url="http://gics3:9000",
        )
        assert create_executors(config)["llamacpp"]._base_url == "http://gics3:9000"

    def test_llamacpp_beside_ollama(self):
        """A small card can serve via Ollama while llama.cpp takes the
        models too large for it."""
        config = DaemonConfig(runtime=["llamacpp", "ollama"])
        executors = create_executors(config)
        assert list(executors) == ["llamacpp", "ollama"]

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
    async def test_refresh_keeps_entries_of_failing_runtime(self, tmp_path):
        # A runtime whose list_models() raised is a FAILED QUERY, not an
        # empty model set: its previous entries must survive the rebuild,
        # or its models would be unroutable for the whole outage window.
        async def raise_list():
            raise RuntimeError("ollama api down")

        ollama = FakeExecutor("ollama", ["a", "mistral:7b"])
        vllm = FakeExecutor("vllm", ["b"])
        worker = _worker({"vllm": vllm, "ollama": ollama}, tmp_path)
        await worker._refresh_model_map()
        assert worker._model_runtimes == {
            "a": "ollama", "mistral:7b": "ollama", "b": "vllm",
        }

        ollama.list_models = raise_list
        await worker._refresh_model_map()
        # vllm's entries rebuilt from the healthy runtime; ollama's
        # carried over from the previous map, so a job for one of them
        # still routes instead of failing as unroutable.
        assert worker._model_runtimes == {
            "a": "ollama", "mistral:7b": "ollama", "b": "vllm",
        }
        assert await worker._executor_for("mistral:7b") is ollama

        # A healthy EMPTY list still clears entries — that is a real
        # "no models" state (e.g. models deleted from the box), not an
        # outage, and must keep working as before.
        ollama.set_names([])
        del ollama.list_models  # back to the class method (a healthy query)
        await worker._refresh_model_map()
        assert worker._model_runtimes == {"b": "vllm"}

    @pytest.mark.asyncio
    async def test_refresh_live_claim_beats_stale_carry_over(self, tmp_path):
        # The failing runtime iterates FIRST here. When the loop used to
        # seed its carry-over entries into the mapping as it went, the
        # stale claim won the setdefault and the healthy runtime's live
        # claim was rejected as a duplicate — routing to the runtime that
        # is down.
        async def raise_list():
            raise RuntimeError("ollama api down")

        ollama = FakeExecutor("ollama", ["qwen3:4b"])
        vllm = FakeExecutor("vllm", ["b"])
        worker = _worker({"ollama": ollama, "vllm": vllm}, tmp_path)
        await worker._refresh_model_map()
        assert worker._model_runtimes == {"qwen3:4b": "ollama", "b": "vllm"}

        # qwen3:4b left ollama and is now served by vllm, but ollama's
        # model list is transiently unreachable during the refresh.
        vllm.set_names(["b", "qwen3:4b"])
        ollama.list_models = raise_list
        await worker._refresh_model_map()
        assert worker._model_runtimes == {"qwen3:4b": "vllm", "b": "vllm"}
        assert await worker._executor_for("qwen3:4b") is vllm

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

    @pytest.mark.asyncio
    async def test_mixed_worker_pulls_model_not_yet_on_box(self, tmp_path):
        # The scheduler dispatched a catalogue model this worker registered
        # but Ollama has not pulled yet: absent from every runtime's
        # list, hence absent from the routing map. A single-runtime
        # Ollama worker always reached ensure_model and pulled it; the
        # mixed worker must take the same path instead of failing the
        # job as unroutable.
        client = MockClient(input_lines=[
            {"custom_id": "p1", "url": "/v1/chat/completions",
             "body": {"messages": [{"role": "user", "content": "hi"}]}},
        ])
        ollama = FakeOllama(["a"])
        vllm = FakeExecutor("vllm", ["b"])
        worker = _worker({"vllm": vllm, "ollama": ollama}, tmp_path, client=client)
        await worker._refresh_model_map()
        assert "mistral:7b" not in worker._model_runtimes

        job = Job(job_id="j1", model="mistral:7b", input_path="/x")
        await worker._execute_job(job)

        assert ollama.pulled == ["mistral:7b"]
        assert client.failure_calls == []
        assert client.upload_calls == [("j1", 1, 0)]
        # The re-resolve after the pull left the map current.
        assert worker._model_runtimes["mistral:7b"] == "ollama"

    @pytest.mark.asyncio
    async def test_mixed_worker_pull_failure_still_fails_job(self, tmp_path):
        # A routing miss that the Ollama pull cannot fix (bad name,
        # registry unreachable) still fails the job — the worker never
        # guesses at a target for it.
        ollama = FakeOllama(["a"], pull_ok=False)
        vllm = FakeExecutor("vllm", ["b"])
        worker = _worker({"vllm": vllm, "ollama": ollama}, tmp_path)
        await worker._refresh_model_map()

        job = Job(job_id="j1", model="ghost", input_path="/x")
        await worker._execute_job(job)

        assert ollama.pulled == ["ghost"]
        assert len(worker._client.failure_calls) == 1
        assert "ghost" in worker._client.failure_calls[0][1]
        assert worker._client.upload_calls == []


# -- Worker id adoption after registration -----------------------------


class TestWorkerIdAdoption:
    def test_update_worker_id_reaches_heartbeat_and_model_manager(self, tmp_path):
        # The backend assigns the worker id at register time — after
        # the Worker is constructed — so the heartbeat and model
        # manager snapshots taken in __init__ stay at the local
        # placeholder until adoption. Before the fix,
        # ModelManager.report_model_download carried the placeholder
        # and /workers/model-progress 404'd, swallowed as a non-fatal
        # debug line.
        ollama = FakeOllama(["a"])
        vllm = FakeExecutor("vllm", ["b"])
        worker = _worker({"vllm": vllm, "ollama": ollama}, tmp_path)
        placeholder = worker._config.worker_id
        assert worker._heartbeat._worker_id == placeholder
        assert worker._model_manager._worker_id == placeholder

        worker.update_worker_id("worker-assigned-123")

        assert worker._heartbeat._worker_id == "worker-assigned-123"
        assert worker._model_manager._worker_id == "worker-assigned-123"

    def test_update_worker_id_without_ollama(self, tmp_path):
        # A vLLM-only node has no ModelManager — adoption must not
        # trip over the missing manager.
        vllm = FakeExecutor("vllm", ["b"])
        worker = _worker({"vllm": vllm}, tmp_path)
        assert worker._model_manager is None
        worker.update_worker_id("worker-assigned-456")
        assert worker._heartbeat._worker_id == "worker-assigned-456"


# -- Heartbeat views: union, per-runtime loaded scope ------------------


class TestHeartbeatViews:
    @pytest.mark.asyncio
    async def test_single_executor_loaded_models_unchanged(self, tmp_path):
        ex = FakeExecutor("ollama", ["a", "b"])
        worker = _worker({"ollama": ex}, tmp_path)
        assert await worker._get_loaded_models() == ["a", "b"]

    @pytest.mark.asyncio
    async def test_bare_executor_reports_no_residence(self, tmp_path):
        """The configured model list names what the worker MAY serve. A
        runtime that cannot report what is in VRAM reports nothing rather
        than dressing that list up as residence."""
        worker = _worker({"bare": BareExecutor()}, tmp_path, models=["static"])
        assert await worker._get_loaded_models() == []

    @pytest.mark.asyncio
    async def test_mixed_worker_unions_across_runtimes(self, tmp_path):
        ollama = FakeExecutor("ollama", ["a", "x"])
        vllm = FakeExecutor("vllm", ["b", "x"])
        worker = _worker({"vllm": vllm, "ollama": ollama}, tmp_path)
        assert await worker._get_loaded_models() == ["b", "x", "a"]

    @pytest.mark.asyncio
    async def test_inventory_union_tagged_and_loaded_scoped(self, tmp_path):
        ollama = FakeExecutor(
            "ollama", ["a"], running=["a"],
            inventory=[{"local_name": "a", "sha256": "a" * 64, "size_bytes": 1}],
        )
        # vLLM holds model "a" on disk too, but has NOT loaded it.
        vllm = FakeExecutor(
            "vllm", ["a", "b"], running=["b"],
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
    async def test_on_disk_but_idle_is_not_loaded(self, tmp_path):
        """Two models installed, one resident -> exactly one `loaded`.

        Stamping `loaded` from the on-disk listing made this test pass with
        both flags true: an on-disk name is always in the on-disk listing.
        """
        ollama = FakeExecutor(
            "ollama", ["hot:1b", "cold:1b"], running=["hot:1b"],
            inventory=[
                {"local_name": "hot:1b", "sha256": "a" * 64, "size_bytes": 1},
                {"local_name": "cold:1b", "sha256": "b" * 64, "size_bytes": 1},
            ],
        )
        worker = _worker({"ollama": ollama}, tmp_path)

        by_name = {i["local_name"]: i for i in await worker._get_inventory()}
        assert by_name["hot:1b"]["loaded"] is True
        assert by_name["cold:1b"]["loaded"] is False
        assert await worker._get_loaded_models() == ["hot:1b"]

    @pytest.mark.asyncio
    async def test_residence_query_failure_loads_nothing(self, tmp_path):
        """A failed residence query is not "everything is resident"."""
        ollama = FakeExecutor(
            "ollama", ["a"],
            inventory=[{"local_name": "a", "sha256": "a" * 64, "size_bytes": 1}],
        )

        async def raise_running():
            raise RuntimeError("runtime down")

        ollama.list_running_models = raise_running
        worker = _worker({"ollama": ollama}, tmp_path)
        assert (await worker._get_inventory())[0]["loaded"] is False

    @pytest.mark.asyncio
    async def test_no_residence_query_when_nothing_to_stamp(self, tmp_path):
        """A runtime reporting no inventory is the shape of one that is
        down. Querying its residence anyway buys nothing and pays a failed
        call — or a timeout — on every beat."""
        ollama = FakeExecutor("ollama", ["a"], inventory=[])
        calls = []

        async def counted():
            calls.append(1)
            return ["a"]

        ollama.list_running_models = counted
        worker = _worker({"ollama": ollama}, tmp_path)

        assert await worker._get_inventory() == []
        assert calls == []

    @pytest.mark.asyncio
    async def test_residence_queried_once_per_runtime(self, tmp_path):
        """Stamping N items costs one residence query, not N."""
        ollama = FakeExecutor(
            "ollama", ["a", "b"], running=["a"],
            inventory=[
                {"local_name": "a", "sha256": "a" * 64, "size_bytes": 1},
                {"local_name": "b", "sha256": "b" * 64, "size_bytes": 1},
            ],
        )
        calls = []
        inner = ollama.list_running_models

        async def counted():
            calls.append(1)
            return await inner()

        ollama.list_running_models = counted
        worker = _worker({"ollama": ollama}, tmp_path)

        by_name = {i["local_name"]: i for i in await worker._get_inventory()}
        assert by_name["a"]["loaded"] is True
        assert by_name["b"]["loaded"] is False
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_pre_stamped_inventory_skips_the_query(self, tmp_path):
        """An executor that already knows residence per item is taken at
        its word — the flag it set is not second-guessed or recomputed."""
        ollama = FakeExecutor(
            "ollama", ["a"], running=[],
            inventory=[{"local_name": "a", "sha256": "a" * 64,
                        "size_bytes": 1, "loaded": True}],
        )
        calls = []

        async def counted():
            calls.append(1)
            return []

        ollama.list_running_models = counted
        worker = _worker({"ollama": ollama}, tmp_path)

        assert (await worker._get_inventory())[0]["loaded"] is True
        assert calls == []

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
    async def test_partial_readiness_keeps_down_runtime(self):
        # The payload must carry one bundle per CONFIGURED runtime,
        # regardless of readiness: the backend's replace-all register
        # deletes any worker_runtimes row missing from the payload (and
        # every runtime_models row under it), and nothing re-registers a
        # dropped runtime. A runtime still loading is advertised with
        # whatever it can report — empty when it can't be queried — and
        # heartbeats fill its row in once it recovers.
        ollama = FakeExecutor("ollama", ["a"])
        vllm = FakeExecutor("vllm", ["b"], healthy=False, detailed=False,
                            inventory=[])
        config = DaemonConfig(runtime=["vllm", "ollama"], models=["static"])
        bundles = await _collect_runtime_bundles(
            config, {"vllm": vllm, "ollama": ollama},
        )
        assert [b.runtime for b in bundles] == ["vllm", "ollama"]
        by_runtime = {b.runtime: b for b in bundles}
        # The down runtime is advertised but reports nothing yet.
        assert by_runtime["vllm"].models == []
        assert by_runtime["vllm"].model_digests == {}
        assert by_runtime["vllm"].inventory == []
        # The ready runtime is unchanged: on a mixed node the bundle
        # models are the dynamic names, not the ambiguous static list.
        assert by_runtime["ollama"].models == ["a"]
        assert by_runtime["ollama"].model_digests == {"a": "digest-a"}

    @pytest.mark.asyncio
    async def test_none_ready_advertises_everything(self):
        ollama = FakeExecutor("ollama", ["a"])
        vllm = FakeExecutor("vllm", ["b"])
        config = DaemonConfig(runtime=["vllm", "ollama"])
        bundles = await _collect_runtime_bundles(
            config, {"vllm": vllm, "ollama": ollama},
        )
        assert [b.runtime for b in bundles] == ["vllm", "ollama"]

    @pytest.mark.asyncio
    async def test_single_runtime_merges_static_and_dynamic(self):
        ollama = FakeExecutor("ollama", ["dyn"])
        config = DaemonConfig(runtime=["ollama"], models=["static"])
        bundles = await _collect_runtime_bundles(
            config, {"ollama": ollama},
        )
        assert bundles[0].models == ["static", "dyn"]
        assert bundles[0].runtime == "ollama"

    @pytest.mark.asyncio
    async def test_mixed_node_splits_static_models_by_claiming_runtime(self):
        # A flat config.models list on a mixed node is attributed per
        # bundle: each entry is advertised by the runtime that reports
        # it in list_models() — the same source the routing map uses.
        # Before the fix the whole list was silently dropped here; the
        # vLLM bundle below has no digests (like the real
        # VLLMExecutor), so without the fix its models stay empty and
        # the configured entry vanishes from the payload.
        ollama = FakeExecutor("ollama", ["a", "mistral:7b"])
        vllm = FakeExecutor("vllm", ["my-model"], detailed=False)
        config = DaemonConfig(
            runtime=["vllm", "ollama"], models=["my-model", "mistral:7b"],
        )
        bundles = await _collect_runtime_bundles(
            config, {"vllm": vllm, "ollama": ollama},
        )
        by_runtime = {b.runtime: b for b in bundles}
        # my-model is reported by vllm only → vllm's bundle.
        assert by_runtime["vllm"].models == ["my-model"]
        assert "my-model" not in by_runtime["ollama"].models
        # mistral:7b is reported by ollama only → ollama's bundle,
        # deduped against the dynamic name, not cross-posted to vllm.
        assert by_runtime["ollama"].models.count("mistral:7b") == 1
        assert "mistral:7b" not in by_runtime["vllm"].models

    @pytest.mark.asyncio
    async def test_mixed_node_logs_unclaimed_static_models(self, caplog):
        # A configured model no runtime reports cannot be attributed to
        # a bundle, so it is not advertised — but the operator is told
        # rather than left guessing (the old code discarded it with no
        # log line at all).
        import logging

        ollama = FakeExecutor("ollama", ["a"])
        vllm = FakeExecutor("vllm", ["b"])
        config = DaemonConfig(runtime=["vllm", "ollama"], models=["ghost"])
        with caplog.at_level(logging.WARNING, logger="daemon.main"):
            bundles = await _collect_runtime_bundles(
                config, {"vllm": vllm, "ollama": ollama},
            )
        by_runtime = {b.runtime: b for b in bundles}
        assert "ghost" not in by_runtime["ollama"].models
        assert "ghost" not in by_runtime["vllm"].models
        assert any(
            "ghost" in record.getMessage()
            for record in caplog.records
            if record.levelno == logging.WARNING
        )

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
            config, {"vllm": vllm, "ollama": ollama},
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
        # Capabilities and version ride along per runtime; empty/None when
        # the bundle was built before the executor could answer.
        assert all("capabilities" in e and "version" in e for e in entries)
        assert entries[0]["models"] == ["m1"]
        assert entries[0]["model_digests"] == {"m1": "d1"}
        assert entries[0]["inventory"][0]["sha256"] == "a" * 64
        assert entries[1]["inventory"] == []


class TestReportsOnlyWhatItVerified:
    """A runtime the daemon never reached must not be advertised as servable."""

    @pytest.mark.asyncio
    async def test_unready_runtime_keeps_its_catalogue_but_says_it_is_down(self, tmp_path):
        """A model on disk is a fact about this worker whether or not its
        runtime answered. The catalogue keeps it; `status` is what stops it
        being dispatched (see Worker.advertised_models)."""
        from daemon.main import _collect_runtime_bundles

        up = FakeExecutor("vllm", ["served:7b"])
        down = FakeExecutor("ollama", ["on-disk:8b"])
        down.inventory_items = [
            {"local_name": "on-disk:8b", "sha256": "d" * 64, "runtime": "ollama"}]

        config = DaemonConfig(api_key="gk-x", runtime=["vllm", "ollama"], models=[])
        bundles = await _collect_runtime_bundles(
            config, {"vllm": up, "ollama": down}, ready=["vllm"])

        by_runtime = {b.runtime: b for b in bundles}
        assert set(by_runtime) == {"vllm", "ollama"}

        assert by_runtime["ollama"].status == "unavailable"
        assert by_runtime["ollama"].inventory, "the on-disk catalogue is not discarded"

        assert by_runtime["vllm"].status == "ready"
        assert by_runtime["vllm"].models == ["served:7b"]

    @pytest.mark.asyncio
    async def test_readiness_unknown_keeps_every_runtime_ready(self, tmp_path):
        """ready=None is 'never established', not 'nothing is ready'."""
        from daemon.main import _collect_runtime_bundles

        ex = FakeExecutor("ollama", ["m1"])
        config = DaemonConfig(api_key="gk-x", runtime=["ollama"], models=[])
        bundles = await _collect_runtime_bundles(config, {"ollama": ex}, ready=None)
        assert bundles[0].status == "ready"
        assert bundles[0].models == ["m1"]

    def test_remote_ollama_never_resolves_a_local_store(self):
        """A manifests tree on this box describes this box's Ollama, not a remote one."""
        from daemon.executors.ollama import OllamaExecutor

        assert OllamaExecutor(base_url="http://localhost:11434")._is_local_server()
        remote = OllamaExecutor(base_url="http://another-box:11434")
        assert not remote._is_local_server()
        assert remote._resolve_models_dir() is None

    def test_explicit_models_dir_is_honoured_even_for_a_remote_server(self, tmp_path):
        """The operator naming a store is them saying it is that server's store."""
        from daemon.executors.ollama import OllamaExecutor

        (tmp_path / "manifests").mkdir()
        remote = OllamaExecutor(base_url="http://another-box:11434",
                                models_dir=str(tmp_path))
        assert remote._resolve_models_dir() == tmp_path
