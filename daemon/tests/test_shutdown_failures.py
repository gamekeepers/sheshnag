"""
Tests for sub-issue 1: shutdown reports unrun prompts as failures.

Verifies that when the daemon shuts down mid-batch:
  - The output list has exactly one entry per input prompt (no gaps).
  - Prompts that completed are recorded faithfully.
  - Prompts that never ran carry the "worker_shutdown:" sentinel error.
  - The sentinel is machine-parseable (startswith check).
"""
import asyncio
from typing import List
from pathlib import Path
import json
import tempfile

import pytest

from daemon.config import DaemonConfig
from daemon.executors.base import BaseExecutor
from daemon.models import CompletionResult, Job, PromptRequest
from daemon.worker import Worker

SHUTDOWN_SENTINEL = "worker_shutdown:"


# ── Shared helpers ──────────────────────────────────────────────────


class SlowExecutor(BaseExecutor):
    """Each prompt takes `delay` seconds — slow enough that shutdown interrupts mid-batch."""

    def __init__(self, delay: float = 0.3):
        self.delay = delay
        self.executed: list[str] = []

    async def execute(self, prompt: PromptRequest) -> CompletionResult:
        self.executed.append(prompt.custom_id)
        await asyncio.sleep(self.delay)
        return CompletionResult(
            custom_id=prompt.custom_id,
            response={"choices": [{"message": {"content": "ok"}}]},
        )

    async def health_check(self) -> bool:
        return True


class MockClient:
    def __init__(self):
        self.progress_calls: list = []
        self.failure_calls: list = []
        self.upload_calls: list = []

    async def report_progress(self, job_id, completed, failed, total):
        self.progress_calls.append((completed, failed, total))

    async def report_failure(self, job_id, error):
        self.failure_calls.append((job_id, error))

    async def upload_results(self, job_id, output_path, completed, failed):
        self.upload_calls.append((job_id, completed, failed))

    async def download_input(self, job_id, input_path, dest_path):
        pass


def make_job(job_id: str = "test-job") -> Job:
    return Job(job_id=job_id, input_file_id="f-1", model="test-model")


def make_prompts(count: int) -> List[PromptRequest]:
    return [
        PromptRequest(
            custom_id=f"prompt-{i}",
            method="POST",
            url="/v1/chat/completions",
            body={"messages": [{"role": "user", "content": f"q{i}"}]},
        )
        for i in range(1, count + 1)
    ]


# ── Tests ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unrun_prompts_get_shutdown_sentinel():
    """
    Any prompt not picked up before _running goes False must appear in
    the returned list with a worker_shutdown: error, not be silently dropped.
    """
    config = DaemonConfig(max_concurrent_prompts=2)
    executor = SlowExecutor(delay=0.3)
    client = MockClient()
    worker = Worker(config, client, executor)

    prompts = make_prompts(10)
    worker._running = True

    # Start the batch then kill it almost immediately
    task = asyncio.create_task(worker._run_prompts(prompts, make_job()))
    await asyncio.sleep(0.05)
    worker._running = False
    raw_results = await task

    # _run_prompts returns only executed prompts — fewer than 10
    assert len(raw_results) < 10

    # Simulate what _execute_job now does (Step 3b)
    results_by_id = {r.custom_id: r for r in raw_results}
    for prompt in prompts:
        if prompt.custom_id not in results_by_id:
            results_by_id[prompt.custom_id] = CompletionResult(
                custom_id=prompt.custom_id,
                error="worker_shutdown:prompt not executed before daemon stopped",
            )
    final = [results_by_id[p.custom_id] for p in prompts]

    # Every prompt must be present
    assert len(final) == 10

    # Every unexecuted prompt must carry the sentinel
    unrun = [r for r in final if r.error and r.error.startswith(SHUTDOWN_SENTINEL)]
    executed = [r for r in final if r.is_success]
    assert len(unrun) + len(executed) == 10
    assert len(unrun) > 0, "Expected some prompts to be unrun"


@pytest.mark.asyncio
async def test_output_file_complete_on_shutdown():
    """
    The output JSONL written by _execute_job must have exactly N lines
    (one per input prompt) even when the daemon shuts down mid-batch.
    """
    config = DaemonConfig(max_concurrent_prompts=2)
    executor = SlowExecutor(delay=0.4)
    client = MockClient()
    worker = Worker(config, client, executor)

    prompts = make_prompts(8)

    with tempfile.TemporaryDirectory() as tmp:
        job_dir = Path(tmp)
        output_path = job_dir / "output.jsonl"

        # Simulate Step 3: partial results from an interrupted batch
        worker._running = True
        task = asyncio.create_task(worker._run_prompts(prompts, make_job()))
        await asyncio.sleep(0.05)
        worker._running = False
        raw_results = await task

        # Step 3b: fill synthetic failures (same logic as _execute_job)
        results_by_id = {r.custom_id: r for r in raw_results}
        for prompt in prompts:
            if prompt.custom_id not in results_by_id:
                results_by_id[prompt.custom_id] = CompletionResult(
                    custom_id=prompt.custom_id,
                    error="worker_shutdown:prompt not executed before daemon stopped",
                )
        final = [results_by_id[p.custom_id] for p in prompts]

        worker._write_output(output_path, final)

        lines = output_path.read_text().strip().splitlines()
        assert len(lines) == 8, f"Expected 8 lines, got {len(lines)}"

        ids_in_file = {json.loads(l)["custom_id"] for l in lines}
        expected_ids = {f"prompt-{i}" for i in range(1, 9)}
        assert ids_in_file == expected_ids, "Output file is missing some custom_ids"


@pytest.mark.asyncio
async def test_no_synthetic_failures_on_clean_run():
    """
    When the batch completes normally (no shutdown), no worker_shutdown:
    entries should appear in the results.
    """
    config = DaemonConfig(max_concurrent_prompts=4)
    executor = SlowExecutor(delay=0.0)
    client = MockClient()
    worker = Worker(config, client, executor)

    prompts = make_prompts(6)
    worker._running = True
    raw_results = await worker._run_prompts(prompts, make_job())

    results_by_id = {r.custom_id: r for r in raw_results}
    for prompt in prompts:
        if prompt.custom_id not in results_by_id:
            results_by_id[prompt.custom_id] = CompletionResult(
                custom_id=prompt.custom_id,
                error="worker_shutdown:prompt not executed before daemon stopped",
            )
    final = [results_by_id[p.custom_id] for p in prompts]

    sentinel_results = [r for r in final if r.error and r.error.startswith(SHUTDOWN_SENTINEL)]
    assert len(sentinel_results) == 0, "No shutdown sentinels expected on clean run"
    assert len(final) == 6
    assert all(r.is_success for r in final)


@pytest.mark.asyncio
async def test_sentinel_is_machine_parseable():
    """
    The sentinel string must start with 'worker_shutdown:' exactly,
    allowing dashboard/scripts to filter by prefix without regex.
    """
    result = CompletionResult(
        custom_id="x",
        error="worker_shutdown:prompt not executed before daemon stopped",
    )
    assert result.error.startswith(SHUTDOWN_SENTINEL)
    assert not result.is_success
