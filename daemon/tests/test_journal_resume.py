"""
Sub-issue 2: journal each result, resume on restart.

  - A hard-killed job leaves its finished rows on disk, and a rerun skips them.
  - The final output has each custom_id exactly once, in input order.
  - A journal truncated mid-line is read without crashing, and appends after it
    land on a fresh line.
  - Failed rows are journalled and skipped too; never-executed placeholders are not.
  - Progress after a resume counts the restored rows.
"""
import asyncio
import json
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from daemon.config import DaemonConfig
from daemon.journal import JOURNAL_FILENAME, ResultJournal, load
from daemon.models import CompletionResult, Job, PromptRequest
from daemon.worker import Worker
from tests.test_shutdown_failures import MockClient, SlowExecutor, make_job, make_prompts

DAEMON_ROOT = Path(__file__).resolve().parent.parent


def _ok(custom_id: str, tokens: int = 7) -> CompletionResult:
    return CompletionResult(
        custom_id=custom_id,
        response={"choices": [], "usage": {"total_tokens": tokens}},
    )


def _worker(tmp_path, executor, client, concurrency=2) -> Worker:
    config = DaemonConfig(work_dir=str(tmp_path), max_concurrent_prompts=concurrency)
    worker = Worker(config, client, {"ollama": executor})
    worker._running = True
    return worker


def _journal_path(tmp_path, job_id="test-job") -> Path:
    return tmp_path / job_id / JOURNAL_FILENAME


# ── The journal file itself ──────────────────────────────────────────


def test_torn_final_line_is_discarded_and_appends_start_clean(tmp_path):
    path = tmp_path / JOURNAL_FILENAME
    path.write_bytes(
        _ok("a").model_dump_json().encode() + b"\n"
        + _ok("b").model_dump_json().encode() + b"\n"
        + b'{"custom_id": "c", "respon'          # killed mid-write
    )

    journal = ResultJournal.open(path)
    assert set(journal.restored) == {"a", "b"}
    journal.append(_ok("d"))
    journal.close()

    assert set(load(path)) == {"a", "b", "d"}
    # The fragment was cut off, not glued onto "d": exactly three whole lines.
    lines = path.read_bytes().split(b"\n")
    assert lines[-1] == b"" and len(lines) == 4
    assert all(json.loads(line) for line in lines[:-1])


def test_complete_final_line_without_newline_is_kept(tmp_path):
    path = tmp_path / JOURNAL_FILENAME
    path.write_bytes(_ok("a").model_dump_json().encode())   # no trailing \n

    journal = ResultJournal.open(path)
    assert set(journal.restored) == {"a"}
    journal.append(_ok("b"))
    journal.close()

    assert set(load(path)) == {"a", "b"}


def test_unreadable_middle_line_is_skipped(tmp_path):
    path = tmp_path / JOURNAL_FILENAME
    path.write_bytes(
        _ok("a").model_dump_json().encode() + b"\n"
        + b"not json\n"
        + b'["valid json", "wrong shape"]\n'
        + _ok("b").model_dump_json().encode() + b"\n"
    )
    assert set(load(path)) == {"a", "b"}


def test_missing_journal_is_empty(tmp_path):
    assert load(tmp_path / "nope.jsonl") == {}


# ── Resume through the production path ───────────────────────────────


@pytest.mark.asyncio
async def test_resume_skips_journalled_rows_and_output_is_complete(tmp_path):
    prompts = make_prompts(6)
    path = _journal_path(tmp_path)
    path.parent.mkdir(parents=True)
    # An earlier run finished prompts 2 and 5 — non-contiguous, as the pool
    # leaves them — and 5 failed.
    path.write_text(
        _ok("prompt-2", tokens=11).model_dump_json() + "\n"
        + CompletionResult(custom_id="prompt-5", error="SCHEMA_VIOLATION: x").model_dump_json() + "\n",
        encoding="utf-8",
    )

    executor = SlowExecutor(delay=0)
    client = MockClient(input_prompts=prompts)
    await _worker(tmp_path, executor, client)._execute_job(make_job())

    assert sorted(executor.executed) == ["prompt-1", "prompt-3", "prompt-4", "prompt-6"]

    lines = client.upload_calls[0]["lines"]
    assert [r["custom_id"] for r in lines] == [p.custom_id for p in prompts]
    assert lines[1]["response"]["usage"]["total_tokens"] == 11
    assert lines[4]["error"] == "SCHEMA_VIOLATION: x"
    assert client.upload_calls[0]["completed"] == 5
    assert client.upload_calls[0]["failed"] == 1

    # Progress reflects all the work, not just this run's four prompts.
    assert client.progress_calls[-1] == (5, 1, 6)

    # Everything is journalled now; a third run would execute nothing.
    assert set(load(path)) == {p.custom_id for p in prompts}


@pytest.mark.asyncio
async def test_failed_rows_are_journalled(tmp_path):
    class FailingExecutor(SlowExecutor):
        async def execute(self, prompt):
            self.executed.append(prompt.custom_id)
            return CompletionResult(custom_id=prompt.custom_id, error="HTTP 400: bad request")

    prompts = make_prompts(3)
    executor = FailingExecutor(delay=0)
    client = MockClient(input_prompts=prompts)
    await _worker(tmp_path, executor, client)._execute_job(make_job())
    assert len(executor.executed) == 3

    rerun = FailingExecutor(delay=0)
    client2 = MockClient(input_prompts=prompts)
    await _worker(tmp_path, rerun, client2)._execute_job(make_job())
    assert rerun.executed == []            # a deterministic failure is not retried
    assert client2.upload_calls[0]["failed"] == 3


@pytest.mark.asyncio
async def test_shutdown_placeholders_are_not_journalled(tmp_path):
    prompts = make_prompts(10)
    client = MockClient(input_prompts=prompts)
    worker = _worker(tmp_path, SlowExecutor(delay=0.2), client)

    task = asyncio.create_task(worker._execute_job(make_job()))
    await asyncio.sleep(0.05)
    worker._running = False
    await task

    journalled = load(_journal_path(tmp_path))
    assert 0 < len(journalled) < 10
    assert not any((r.error or "").startswith("worker_shutdown:") for r in journalled.values())

    # The resume runs exactly the rows the shutdown left unrun.
    rerun = SlowExecutor(delay=0)
    await _worker(tmp_path, rerun, MockClient(input_prompts=prompts))._execute_job(make_job())
    assert sorted(rerun.executed) == sorted(set(p.custom_id for p in prompts) - set(journalled))


@pytest.mark.asyncio
async def test_duplicate_row_does_not_inherit_journalled_result(tmp_path):
    prompts = make_prompts(2)
    prompts.append(prompts[0].model_copy())        # bypassed validator: prompt-1 twice
    path = _journal_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(_ok("prompt-1").model_dump_json() + "\n", encoding="utf-8")

    client = MockClient(input_prompts=prompts)
    await _worker(tmp_path, SlowExecutor(delay=0), client)._execute_job(make_job())

    lines = client.upload_calls[0]["lines"]
    assert lines[0]["response"] is not None
    assert lines[2]["error"].startswith("DUPLICATE_CUSTOM_ID")


# ── kill -9 mid-batch ────────────────────────────────────────────────

_CHILD = textwrap.dedent('''
    import asyncio, sys
    sys.path.insert(0, sys.argv[1])
    sys.path.insert(0, sys.argv[2])
    from daemon.config import DaemonConfig
    from daemon.worker import Worker
    from tests.test_shutdown_failures import MockClient, SlowExecutor, make_job, make_prompts

    async def main():
        config = DaemonConfig(work_dir=sys.argv[3], max_concurrent_prompts=2)
        client = MockClient(input_prompts=make_prompts(40))
        worker = Worker(config, client, {"ollama": SlowExecutor(delay=0.1)})
        worker._running = True
        await worker._execute_job(make_job())
        print("FINISHED", flush=True)   # must never be reached

    asyncio.run(main())
''')


@pytest.mark.asyncio
async def test_hard_kill_mid_batch_then_resume(tmp_path):
    """A process killed without any cleanup still leaves a resumable journal."""
    work_dir = tmp_path / "work"
    script = tmp_path / "child.py"
    script.write_text(_CHILD, encoding="utf-8")
    path = _journal_path(work_dir)

    proc = subprocess.Popen(
        [sys.executable, str(script), str(DAEMON_ROOT), str(DAEMON_ROOT / "tests" / ".."), str(work_dir)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if path.exists() and path.read_bytes().count(b"\n") >= 6:
                break
            if proc.poll() is not None:
                pytest.fail(f"child exited early: {proc.stderr.read().decode()}")
            await asyncio.sleep(0.02)
        else:
            pytest.fail("child never journalled anything")
        proc.kill()                       # SIGKILL on POSIX, TerminateProcess on Windows
        proc.wait(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
    assert b"FINISHED" not in proc.stdout.read()

    done_before = set(load(path))
    assert 6 <= len(done_before) < 40

    prompts = make_prompts(40)
    rerun = SlowExecutor(delay=0)
    client = MockClient(input_prompts=prompts)
    await _worker(work_dir, rerun, client)._execute_job(make_job())

    # Completed prompts were skipped, not re-executed.
    assert not (set(rerun.executed) & done_before)
    assert len(rerun.executed) == 40 - len(done_before)

    ids = [r["custom_id"] for r in client.upload_calls[0]["lines"]]
    assert ids == [p.custom_id for p in prompts]   # each exactly once, in order
    assert client.upload_calls[0]["completed"] == 40
    assert client.progress_calls[-1] == (40, 0, 40)
