"""
Tests for sub-issue 1: shutdown reports unrun prompts as failures.

Verifies that when the daemon shuts down mid-batch:
  - Reconciliation happens through the production execution path (_execute_job).
  - The uploaded output JSONL has exactly one entry per input prompt (no gaps).
  - Prompts that completed are recorded faithfully.
  - Prompts that never ran carry the "worker_shutdown:" sentinel error.
  - The sentinel is machine-parseable (startswith check) on actual production output.
  - Duplicate custom IDs are preserved and never collapsed.
"""
import asyncio
import json
from pathlib import Path
from typing import List

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
        if self.delay > 0:
            await asyncio.sleep(self.delay)
        return CompletionResult(
            custom_id=prompt.custom_id,
            response={"choices": [{"message": {"content": "ok"}}]},
        )

    async def health_check(self) -> bool:
        return True


class MockClient:
    def __init__(self, input_prompts: List[PromptRequest] | None = None):
        self.input_prompts = input_prompts or []
        self.progress_calls: list = []
        self.failure_calls: list = []
        self.upload_calls: list = []

    async def report_progress(self, job_id, completed, failed, total):
        self.progress_calls.append((completed, failed, total))

    async def report_failure(self, job_id, error):
        self.failure_calls.append((job_id, error))

    async def upload_results(self, job_id, output_path, completed, failed):
        content = Path(output_path).read_text(encoding="utf-8")
        lines = [
            json.loads(line)
            for line in content.strip().splitlines()
            if line.strip()
        ]
        self.upload_calls.append({
            "job_id": job_id,
            "output_path": output_path,
            "completed": completed,
            "failed": failed,
            "content": content,
            "lines": lines,
        })

    async def download_input(self, job_id, input_path, dest_path):
        Path(dest_path).parent.mkdir(parents=True, exist_ok=True)
        with open(dest_path, "w", encoding="utf-8") as fh:
            for p in self.input_prompts:
                fh.write(p.model_dump_json() + "\n")


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
async def test_unrun_prompts_get_shutdown_sentinel(tmp_path):
    """
    Any prompt not picked up before _running goes False must appear in
    the uploaded output with a worker_shutdown: error, not be silently dropped.
    """
    config = DaemonConfig(work_dir=str(tmp_path), max_concurrent_prompts=2)
    executor = SlowExecutor(delay=0.3)
    prompts = make_prompts(10)
    client = MockClient(input_prompts=prompts)
    worker = Worker(config, client, {"ollama": executor})
    worker._running = True

    # Start the job execution and interrupt it mid-batch
    task = asyncio.create_task(worker._execute_job(make_job()))
    await asyncio.sleep(0.05)
    worker._running = False
    await task

    assert len(client.upload_calls) == 1
    upload = client.upload_calls[0]
    lines = upload["lines"]
    assert len(lines) == 10

    unrun = [r for r in lines if r.get("error") and r["error"].startswith(SHUTDOWN_SENTINEL)]
    executed = [r for r in lines if "response" in r and r["response"]]
    assert len(unrun) + len(executed) == 10
    assert len(unrun) > 0, "Expected some prompts to be unrun"
    assert upload["completed"] == len(executed)
    assert upload["failed"] == len(unrun)


@pytest.mark.asyncio
async def test_output_file_complete_on_shutdown(tmp_path):
    """
    The output JSONL uploaded by _execute_job must have exactly N lines
    (one per input prompt) in input order even when the daemon shuts down mid-batch.
    """
    config = DaemonConfig(work_dir=str(tmp_path), max_concurrent_prompts=2)
    executor = SlowExecutor(delay=0.4)
    prompts = make_prompts(8)
    client = MockClient(input_prompts=prompts)
    worker = Worker(config, client, {"ollama": executor})
    worker._running = True

    task = asyncio.create_task(worker._execute_job(make_job()))
    await asyncio.sleep(0.05)
    worker._running = False
    await task

    assert len(client.upload_calls) == 1
    upload = client.upload_calls[0]
    lines = upload["lines"]
    assert len(lines) == 8, f"Expected 8 lines, got {len(lines)}"

    uploaded_ids = [r["custom_id"] for r in lines]
    expected_ids = [f"prompt-{i}" for i in range(1, 9)]
    assert uploaded_ids == expected_ids, "Uploaded output must preserve exact input order"


@pytest.mark.asyncio
async def test_duplicate_custom_ids_preserved_on_shutdown(tmp_path):
    """
    Duplicate custom_ids in input must not be collapsed during shutdown reconciliation.
    Each row keeps its own result in input order.
    """
    config = DaemonConfig(work_dir=str(tmp_path), max_concurrent_prompts=1)
    executor = SlowExecutor(delay=0.3)
    prompts = [
        PromptRequest(
            custom_id="prompt-dup",
            method="POST",
            url="/v1/chat/completions",
            body={"messages": [{"role": "user", "content": "1"}]},
        ),
        PromptRequest(
            custom_id="prompt-dup",
            method="POST",
            url="/v1/chat/completions",
            body={"messages": [{"role": "user", "content": "2"}]},
        ),
        PromptRequest(
            custom_id="prompt-later",
            method="POST",
            url="/v1/chat/completions",
            body={"messages": [{"role": "user", "content": "3"}]},
        ),
    ]
    client = MockClient(input_prompts=prompts)
    worker = Worker(config, client, {"ollama": executor})
    worker._running = True

    task = asyncio.create_task(worker._execute_job(make_job()))
    await asyncio.sleep(0.05)
    worker._running = False
    await task

    assert len(client.upload_calls) == 1
    lines = client.upload_calls[0]["lines"]
    assert len(lines) == 3, f"Expected 3 rows, got {len(lines)}"

    # Row 0 ran or had in-flight execution
    # Row 1 is a duplicate error DUPLICATE_CUSTOM_ID
    # Row 2 was unrun due to shutdown
    assert lines[1]["error"].startswith("DUPLICATE_CUSTOM_ID")
    assert lines[2]["error"].startswith(SHUTDOWN_SENTINEL)
    # Both duplicate custom_ids exist as separate output rows
    assert lines[0]["custom_id"] == "prompt-dup"
    assert lines[1]["custom_id"] == "prompt-dup"
    assert lines[2]["custom_id"] == "prompt-later"


@pytest.mark.asyncio
async def test_no_synthetic_failures_on_clean_run(tmp_path):
    """
    When the batch completes normally (no shutdown), no worker_shutdown:
    entries should appear in the uploaded output.
    """
    config = DaemonConfig(work_dir=str(tmp_path), max_concurrent_prompts=4)
    executor = SlowExecutor(delay=0.0)
    prompts = make_prompts(6)
    client = MockClient(input_prompts=prompts)
    worker = Worker(config, client, {"ollama": executor})
    worker._running = True

    await worker._execute_job(make_job())

    assert len(client.upload_calls) == 1
    upload = client.upload_calls[0]
    lines = upload["lines"]
    sentinel_results = [r for r in lines if r.get("error") and r["error"].startswith(SHUTDOWN_SENTINEL)]
    assert len(sentinel_results) == 0, "No shutdown sentinels expected on clean run"
    assert len(lines) == 6
    assert upload["completed"] == 6
    assert upload["failed"] == 0


@pytest.mark.asyncio
async def test_crashed_pool_worker_rows_are_not_labelled_shutdown(tmp_path):
    """
    Rows stranded by a crashed pool worker (no shutdown) still get an output
    row each, but must not carry the shutdown sentinel.
    """
    config = DaemonConfig(work_dir=str(tmp_path), max_concurrent_prompts=1)
    executor = SlowExecutor(delay=0.0)
    prompts = make_prompts(3)
    client = MockClient(input_prompts=prompts)
    worker = Worker(config, client, {"ollama": executor})
    worker._running = True

    def _broken_heartbeat(*args, **kwargs):
        raise RuntimeError("heartbeat exploded")

    # Breaks the pool loop itself, after the first row is recorded.
    worker._heartbeat.update_status = _broken_heartbeat
    results = await worker._run_prompts(prompts, make_job(), executor)

    assert len(results) == 3
    assert results[0].is_success
    for r in results[1:]:
        assert r.error.startswith("INTERNAL_ERROR")
        assert not r.error.startswith(SHUTDOWN_SENTINEL)


@pytest.mark.asyncio
async def test_sentinel_is_machine_parseable(tmp_path):
    """
    The sentinel error produced by _execute_job must start with 'worker_shutdown:' exactly,
    allowing dashboard/scripts to filter by prefix without regex.
    """
    config = DaemonConfig(work_dir=str(tmp_path), max_concurrent_prompts=1)
    executor = SlowExecutor(delay=0.5)
    prompts = make_prompts(2)
    client = MockClient(input_prompts=prompts)
    worker = Worker(config, client, {"ollama": executor})
    worker._running = False  # Shut down before execution begins

    await worker._execute_job(make_job())

    assert len(client.upload_calls) == 1
    lines = client.upload_calls[0]["lines"]
    assert len(lines) == 2
    assert lines[0]["error"].startswith(SHUTDOWN_SENTINEL)
    assert lines[1]["error"].startswith(SHUTDOWN_SENTINEL)
