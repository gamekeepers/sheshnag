"""
Graceful shutdown leaves the batch resumable, not truncated (#168).

Supersedes sub-issue 1's behaviour (upload unrun rows as worker_shutdown:
failures), which completed the batch and destroyed the work in progress.

Verifies that when the daemon is stopped mid-batch:
  - Nothing is uploaded, and no never-attempted prompt is reported as failed.
  - The batch is handed back with shutdown=True (no attempt spent) and the
    journalled count.
  - Stopping, then restarting, continues the batch: only the unfinished rows
    run, and the output has each row exactly once, in input order.
  - Prompts already running get shutdown_grace_seconds to finish; after that
    they are cancelled, so a stop completes promptly.
  - A hand-back the backend did not acknowledge is remembered as graceful in
    the marker, so the restart's hand-back is still free.
  - A clean run and a crashed pool worker behave as before.

The helpers at the top are shared with other test modules.
"""
import asyncio
import json
import time
from pathlib import Path
from typing import List

import pytest

from daemon.config import DaemonConfig
from daemon.executors.base import BaseExecutor
from daemon.journal import JOURNAL_FILENAME, load
from daemon.models import CompletionResult, Job, PromptRequest
from daemon.worker import IN_FLIGHT_MARKER, SHUTDOWN_MARKER_TEXT, Worker

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
        self.failure_kwargs: list = []
        self.upload_calls: list = []
        self.delivered = True

    async def report_progress(self, job_id, completed, failed, total):
        self.progress_calls.append((completed, failed, total))

    async def report_failure(self, job_id, error, journalled=None, shutdown=False):
        self.failure_calls.append((job_id, error))
        self.failure_kwargs.append({"journalled": journalled, "shutdown": shutdown})
        return self.delivered

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


def _worker(tmp_path, executor, client, concurrency=2, grace=30.0) -> Worker:
    config = DaemonConfig(
        work_dir=str(tmp_path), max_concurrent_prompts=concurrency,
        shutdown_grace_seconds=grace,
    )
    worker = Worker(config, client, {"ollama": executor})
    worker._running = True
    return worker


class PollOnceClient(MockClient):
    """Serves `job` on the first poll, nothing after."""

    def __init__(self, *args, job=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.job = job

    async def poll_job(self):
        job, self.job = self.job, None
        return job


# ── A stop mid-batch hands the batch back instead of truncating it ───


@pytest.mark.asyncio
async def test_stop_mid_batch_uploads_nothing_and_hands_back(tmp_path):
    prompts = make_prompts(10)
    client = MockClient(input_prompts=prompts)
    worker = _worker(tmp_path, SlowExecutor(delay=0.2), client)

    task = asyncio.create_task(worker._execute_job(make_job()))
    await asyncio.sleep(0.05)
    worker.request_stop()
    acknowledged = await task

    assert acknowledged is True
    assert client.upload_calls == []                 # never completed as final
    assert len(client.failure_calls) == 1
    job_id, error = client.failure_calls[0]
    assert job_id == "test-job" and error.startswith(SHUTDOWN_SENTINEL)

    journalled = load(tmp_path / "test-job" / JOURNAL_FILENAME)
    assert 0 < len(journalled) < 10
    assert client.failure_kwargs[0] == {"journalled": len(journalled), "shutdown": True}


@pytest.mark.asyncio
async def test_no_never_attempted_prompt_is_reported_failed(tmp_path):
    prompts = make_prompts(10)
    client = MockClient(input_prompts=prompts)
    worker = _worker(tmp_path, SlowExecutor(delay=0.2), client)

    task = asyncio.create_task(worker._execute_job(make_job()))
    await asyncio.sleep(0.05)
    worker.request_stop()
    await task

    # Progress reports count only rows that actually ran.
    assert all(failed == 0 for _, failed, _ in client.progress_calls)
    assert client.upload_calls == []


@pytest.mark.asyncio
async def test_stop_then_restart_continues_the_batch(tmp_path):
    prompts = make_prompts(10)
    first = SlowExecutor(delay=0.2)
    client = MockClient(input_prompts=prompts)
    worker = _worker(tmp_path, first, client)

    task = asyncio.create_task(worker._execute_job(make_job()))
    await asyncio.sleep(0.05)
    worker.request_stop()
    await task
    done_before = set(load(tmp_path / "test-job" / JOURNAL_FILENAME))

    # The batch was requeued; it comes back to this worker after the restart.
    rerun = SlowExecutor(delay=0)
    client2 = MockClient(input_prompts=prompts)
    await _worker(tmp_path, rerun, client2)._execute_job(make_job())

    assert not (set(rerun.executed) & done_before)
    assert len(rerun.executed) == 10 - len(done_before)
    lines = client2.upload_calls[0]["lines"]
    assert [r["custom_id"] for r in lines] == [p.custom_id for p in prompts]
    assert client2.upload_calls[0]["completed"] == 10
    assert client2.upload_calls[0]["failed"] == 0
    assert not any((r.get("error") or "").startswith(SHUTDOWN_SENTINEL) for r in lines)


@pytest.mark.asyncio
async def test_stop_before_execution_hands_back_with_nothing_journalled(tmp_path):
    client = MockClient(input_prompts=make_prompts(2))
    worker = _worker(tmp_path, SlowExecutor(delay=0.5), client)
    worker.request_stop()                            # stopped before the job ran

    assert await worker._execute_job(make_job()) is True
    assert client.upload_calls == []
    assert client.failure_kwargs == [{"journalled": 0, "shutdown": True}]


@pytest.mark.asyncio
async def test_duplicate_custom_ids_on_stop_are_not_reported(tmp_path):
    """A duplicate row is rejected before execution, so it is rebuilt on the
    resume; a stop must not upload it (or anything) as a partial result."""
    prompts = make_prompts(3)
    prompts.insert(1, prompts[0].model_copy())
    client = MockClient(input_prompts=prompts)
    worker = _worker(tmp_path, SlowExecutor(delay=0.3), client, concurrency=1)

    task = asyncio.create_task(worker._execute_job(make_job()))
    await asyncio.sleep(0.05)
    worker.request_stop()
    await task

    assert client.upload_calls == []
    assert client.failure_kwargs[0]["shutdown"] is True


# ── The stop is bounded ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_in_flight_prompts_finish_within_the_grace_period(tmp_path):
    prompts = make_prompts(6)
    client = MockClient(input_prompts=prompts)
    worker = _worker(tmp_path, SlowExecutor(delay=0.3), client, concurrency=2, grace=5.0)

    task = asyncio.create_task(worker._execute_job(make_job()))
    await asyncio.sleep(0.05)                        # two prompts in flight
    worker.request_stop()
    await task

    # The two that were running when the stop came were allowed to finish.
    assert set(load(tmp_path / "test-job" / JOURNAL_FILENAME)) == {"prompt-1", "prompt-2"}


@pytest.mark.asyncio
async def test_grace_period_cuts_off_slow_prompts(tmp_path):
    prompts = make_prompts(4)
    client = MockClient(input_prompts=prompts)
    executor = SlowExecutor(delay=30)                # far past any sane stop
    worker = _worker(tmp_path, executor, client, concurrency=2, grace=0.2)

    task = asyncio.create_task(worker._execute_job(make_job()))
    await asyncio.sleep(0.05)
    started = time.monotonic()
    worker.request_stop()
    await asyncio.wait_for(task, timeout=5)

    assert time.monotonic() - started < 2
    # Cancelled prompts never finished, so none are journalled; the resume
    # runs them again.
    assert load(tmp_path / "test-job" / JOURNAL_FILENAME) == {}
    assert client.upload_calls == []
    assert client.failure_kwargs == [{"journalled": 0, "shutdown": True}]


# ── Hand-back that does not get through ─────────────────────────────


@pytest.mark.asyncio
async def test_undelivered_graceful_hand_back_stays_graceful_after_restart(tmp_path):
    prompts = make_prompts(6)
    client = PollOnceClient(input_prompts=prompts, job=make_job())
    client.delivered = False                         # backend down during the stop
    worker = _worker(tmp_path, SlowExecutor(delay=0.2), client)

    task = asyncio.create_task(worker._poll_and_execute())
    await asyncio.sleep(0.3)
    worker.request_stop()
    await task

    marker = tmp_path / "test-job" / IN_FLIGHT_MARKER
    assert marker.read_text(encoding="utf-8") == SHUTDOWN_MARKER_TEXT

    # Restart: the hand-back before the first poll is still a graceful one.
    restarted_client = PollOnceClient()
    await _worker(tmp_path, SlowExecutor(delay=0), restarted_client)._poll_and_execute()
    assert restarted_client.failure_calls[0][0] == "test-job"
    assert restarted_client.failure_kwargs[0]["shutdown"] is True
    assert not marker.exists()


@pytest.mark.asyncio
async def test_crash_marker_is_not_treated_as_graceful(tmp_path):
    (tmp_path / "batch-a").mkdir()
    (tmp_path / "batch-a" / IN_FLIGHT_MARKER).touch()     # left by a kill -9

    client = PollOnceClient()
    await _worker(tmp_path, SlowExecutor(delay=0), client)._poll_and_execute()
    assert client.failure_kwargs[0]["shutdown"] is False


# ── A real SIGTERM, end to end ──────────────────────────────────────

_SIGTERM_CHILD = '''
import asyncio, json, sys
sys.path.insert(0, sys.argv[1])
from daemon.config import DaemonConfig
from daemon.worker import Worker
from tests.test_shutdown_failures import MockClient, SlowExecutor, make_job, make_prompts

class Client(MockClient):
    async def poll_job(self):
        return make_job()

async def main():
    config = DaemonConfig(work_dir=sys.argv[2], max_concurrent_prompts=2,
                          shutdown_grace_seconds=5)
    client = Client(input_prompts=make_prompts(40))
    worker = Worker(config, client, {"ollama": SlowExecutor(delay=0.1)})
    worker._running = True
    worker._install_signal_handlers()
    await worker._poll_and_execute()
    print(json.dumps({"uploads": len(client.upload_calls),
                      "hand_backs": client.failure_kwargs}), flush=True)

asyncio.run(main())
'''


@pytest.mark.asyncio
@pytest.mark.skipif(
    __import__("sys").platform == "win32",
    reason="asyncio signal handlers are not available on Windows event loops",
)
async def test_sigterm_mid_batch_then_restart_continues(tmp_path):
    import signal
    import subprocess
    import sys

    work_dir = tmp_path / "work"
    script = tmp_path / "child.py"
    script.write_text(_SIGTERM_CHILD, encoding="utf-8")
    journal = work_dir / "test-job" / JOURNAL_FILENAME
    daemon_root = Path(__file__).resolve().parent.parent

    proc = subprocess.Popen(
        [sys.executable, str(script), str(daemon_root), str(work_dir)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if journal.exists() and journal.read_bytes().count(b"\n") >= 6:
                break
            if proc.poll() is not None:
                pytest.fail(f"child exited early: {proc.stderr.read().decode()}")
            await asyncio.sleep(0.02)
        else:
            pytest.fail("child never journalled anything")
        proc.send_signal(signal.SIGTERM)
        out, err = proc.communicate(timeout=15)
    finally:
        if proc.poll() is None:
            proc.kill()

    assert proc.returncode == 0, err.decode()
    report = json.loads(out.decode().strip().splitlines()[-1])
    done_before = set(load(journal))
    assert report["uploads"] == 0
    assert report["hand_backs"] == [{"journalled": len(done_before), "shutdown": True}]
    assert 6 <= len(done_before) < 40
    assert not (work_dir / "test-job" / IN_FLIGHT_MARKER).exists()   # acknowledged

    # Restart: the requeued batch comes back here and only the rest runs.
    prompts = make_prompts(40)
    rerun = SlowExecutor(delay=0)
    client = MockClient(input_prompts=prompts)
    await _worker(work_dir, rerun, client)._execute_job(make_job())
    assert not (set(rerun.executed) & done_before)
    assert [r["custom_id"] for r in client.upload_calls[0]["lines"]] == [p.custom_id for p in prompts]
    assert client.upload_calls[0]["completed"] == 40


# ── Unchanged behaviour ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_synthetic_failures_on_clean_run(tmp_path):
    prompts = make_prompts(6)
    client = MockClient(input_prompts=prompts)
    await _worker(tmp_path, SlowExecutor(delay=0.0), client, concurrency=4)._execute_job(make_job())

    assert len(client.upload_calls) == 1
    upload = client.upload_calls[0]
    assert len(upload["lines"]) == 6
    assert upload["completed"] == 6
    assert upload["failed"] == 0
    assert client.failure_calls == []


@pytest.mark.asyncio
async def test_crashed_pool_worker_rows_are_not_labelled_shutdown(tmp_path):
    """
    Rows stranded by a crashed pool worker (no shutdown) still get an output
    row each, but must not carry the shutdown sentinel.
    """
    prompts = make_prompts(3)
    executor = SlowExecutor(delay=0.0)
    worker = _worker(tmp_path, executor, MockClient(input_prompts=prompts), concurrency=1)

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
