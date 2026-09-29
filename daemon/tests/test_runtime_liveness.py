"""Per-beat runtime liveness (#141).

Startup readiness answers "can this worker serve anything yet". Once a
provider stops one runtime and starts another, the answer changes without
the daemon restarting, so the heartbeat has to carry it.
"""
import asyncio

import pytest

from daemon.config import DaemonConfig
from daemon.executors.base import BaseExecutor
from daemon.models import CompletionResult, PromptRequest
from daemon.worker import Worker


class FlippableExecutor(BaseExecutor):
    """A runtime whose reachability a test can toggle mid-run."""

    def __init__(self, runtime, names, healthy=True):
        self.runtime_name = runtime
        self._names = list(names)
        self.healthy = healthy
        self.health_calls = 0
        self.list_calls = 0

    async def execute(self, prompt: PromptRequest) -> CompletionResult:
        return CompletionResult(custom_id=prompt.custom_id, response={})

    async def health_check(self) -> bool:
        self.health_calls += 1
        if not self.healthy:
            raise ConnectionError("connection refused")
        return True

    async def list_models(self):
        self.list_calls += 1
        if not self.healthy:
            raise ConnectionError("connection refused")
        return list(self._names)

    async def list_running_models(self):
        return await self.list_models()

    async def inventory(self):
        if not self.healthy:
            return []
        return self.tag_inventory(
            [{"local_name": n, "sha256": None, "size_bytes": None} for n in self._names]
        )


class HangingExecutor(FlippableExecutor):
    """Accepts the probe and never answers."""

    async def health_check(self) -> bool:
        self.health_calls += 1
        await asyncio.sleep(3600)
        return True


def _worker(executors, tmp_path):
    config = DaemonConfig(work_dir=str(tmp_path / "jobs"))
    return Worker(config, None, executors)


@pytest.fixture
def fast_readiness(monkeypatch):
    """Startup readiness retries a down runtime for a minute by design.
    A test that wants the give-up path should not wait for it."""
    from daemon import worker as worker_module
    monkeypatch.setattr(worker_module, "READINESS_MAX_RETRIES", 1)
    monkeypatch.setattr(worker_module, "READINESS_RETRY_DELAY", 0.0)


@pytest.mark.asyncio
async def test_statuses_report_each_runtime(tmp_path):
    executors = {
        "vllm": FlippableExecutor("vllm", ["Org/Served"]),
        "ollama": FlippableExecutor("ollama", ["qwen3:4b"], healthy=False),
    }
    worker = _worker(executors, tmp_path)

    statuses = await worker._get_runtime_statuses()

    assert statuses == [
        {"type": "vllm", "status": "ready"},
        {"type": "ollama", "status": "unavailable"},
    ]


@pytest.mark.asyncio
async def test_runtime_coming_up_becomes_routable_without_a_restart(
    tmp_path, fast_readiness,
):
    """The point of the issue: a runtime that arrives after startup must
    enter the routing map, which is otherwise built once.

    Two runtimes, because a single-runtime worker routes everything to its
    one executor without consulting the map at all.
    """
    vllm = FlippableExecutor("vllm", ["Org/Served"])
    ollama = FlippableExecutor("ollama", ["qwen3:4b"], healthy=False)
    worker = _worker({"vllm": vllm, "ollama": ollama}, tmp_path)

    await worker.wait_for_runtimes()
    assert "qwen3:4b" not in worker._model_runtimes      # down at startup
    assert await worker._executor_for("qwen3:4b") is None

    ollama.healthy = True
    statuses = await worker._get_runtime_statuses()

    assert statuses == [
        {"type": "vllm", "status": "ready"},
        {"type": "ollama", "status": "ready"},
    ]
    assert worker._model_runtimes["qwen3:4b"] == "ollama"
    assert await worker._executor_for("qwen3:4b") is ollama


@pytest.mark.asyncio
async def test_no_change_does_not_rebuild_the_map(tmp_path):
    """The rebuild is a per-runtime model listing; a steady state must not
    pay for it on every beat."""
    ollama = FlippableExecutor("ollama", ["qwen3:4b"])
    worker = _worker({"ollama": ollama}, tmp_path)
    await worker._get_runtime_statuses()

    calls = {"n": 0}
    original = worker._refresh_model_map

    async def counting():
        calls["n"] += 1
        await original()

    worker._refresh_model_map = counting
    await worker._get_runtime_statuses()
    await worker._get_runtime_statuses()

    assert calls["n"] == 0


@pytest.mark.asyncio
async def test_a_hanging_probe_reports_unavailable(tmp_path):
    """A runtime that accepts the connection and stalls must not stretch
    the beat: the probe is bounded and the verdict is unavailable."""
    from daemon import worker as worker_module

    worker = _worker({"vllm": HangingExecutor("vllm", ["Org/Served"])}, tmp_path)
    original = worker_module.LIVENESS_PROBE_TIMEOUT
    worker_module.LIVENESS_PROBE_TIMEOUT = 0.05
    try:
        statuses = await asyncio.wait_for(worker._get_runtime_statuses(), timeout=5)
    finally:
        worker_module.LIVENESS_PROBE_TIMEOUT = original

    assert statuses == [{"type": "vllm", "status": "unavailable"}]


@pytest.mark.asyncio
async def test_startup_readiness_seeds_the_state(tmp_path):
    """A runtime healthy at startup must not read as a down-to-up change on
    the first beat."""
    ollama = FlippableExecutor("ollama", ["qwen3:4b"])
    worker = _worker({"ollama": ollama}, tmp_path)
    await worker.wait_for_runtimes()

    calls = {"n": 0}
    original = worker._refresh_model_map

    async def counting():
        calls["n"] += 1
        await original()

    worker._refresh_model_map = counting
    assert await worker._get_runtime_statuses() == [
        {"type": "ollama", "status": "ready"}
    ]
    assert calls["n"] == 0


@pytest.mark.asyncio
async def test_heartbeat_payload_carries_the_statuses(tmp_path):
    from daemon.heartbeat import HeartbeatManager

    worker = _worker({"ollama": FlippableExecutor("ollama", ["qwen3:4b"])}, tmp_path)
    hb = HeartbeatManager(
        client=None, worker_id="w",
        get_runtime_statuses=worker._get_runtime_statuses,
    )
    payload = await hb._build_payload()

    assert payload["runtimes"] == [{"type": "ollama", "status": "ready"}]


@pytest.mark.asyncio
async def test_payload_omits_statuses_when_nothing_supplies_them():
    """An empty list is how the backend recognises a daemon that cannot
    report liveness, and it must not be confused with "all down"."""
    from daemon.heartbeat import HeartbeatManager

    hb = HeartbeatManager(client=None, worker_id="w")
    payload = await hb._build_payload()

    assert payload["runtimes"] == []


@pytest.mark.asyncio
async def test_a_transition_lists_each_runtime_once(tmp_path):
    """The map rebuild already fetched every runtime's model list; logging
    what each serves must read that, not fetch it again."""
    vllm = FlippableExecutor("vllm", ["Org/Served"])
    ollama = FlippableExecutor("ollama", ["qwen3:4b"])
    worker = _worker({"vllm": vllm, "ollama": ollama}, tmp_path)

    await worker._get_runtime_statuses()      # first beat is a transition

    assert vllm.list_calls == 1
    assert ollama.list_calls == 1
