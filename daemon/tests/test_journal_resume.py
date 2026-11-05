"""
Sub-issue 2: journal each result, resume on restart.

  - A hard-killed job leaves its finished rows on disk; the restarted daemon
    hands the batch back, and when it returns only the remainder runs.
  - The final output has each custom_id exactly once, in input order.
  - A journal truncated mid-line is read without crashing, and appends after it
    land on a fresh line.
  - Deterministic failures are journalled and skipped; transient ones and
    never-executed placeholders are not.
  - Progress after a resume counts the restored rows.
  - A journal that cannot be written never costs executed work.
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
from daemon.journal import JOURNAL_FILENAME, ResultJournal, is_final, load
from daemon.models import CompletionResult
from daemon.worker import IN_FLIGHT_MARKER, Worker
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


class UploadFailsClient(MockClient):
    """An upload that errors: the job is reported failed and requeued, and the
    journal stays for the rerun — the path a resume actually takes."""

    async def upload_results(self, job_id, output_path, completed, failed):
        raise OSError("connection reset during upload")


class ReclaimClient(MockClient):
    def __init__(self, *args, delivered=True, job=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.delivered = delivered
        self.job = job

    async def report_failure(self, job_id, error):
        self.failure_calls.append((job_id, error))
        return self.delivered

    async def poll_job(self):
        job, self.job = self.job, None
        return job


class ErrorExecutor(SlowExecutor):
    """Answers each prompt with the error ``error_for`` gives it (None = success)."""

    def __init__(self, error_for):
        super().__init__(delay=0)
        self.error_for = error_for

    async def execute(self, prompt):
        self.executed.append(prompt.custom_id)
        error = self.error_for(prompt.custom_id)
        if error is None:
            return _ok(prompt.custom_id)
        return CompletionResult(custom_id=prompt.custom_id, error=error)


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
    journal.append([_ok("d")])
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
    journal.append([_ok("b")])
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


@pytest.mark.parametrize("error,final", [
    ("HTTP 400 for prompt p: bad request", True),
    ("Client error '422 Unprocessable Entity' for url 'http://x'", True),
    ("SCHEMA_VIOLATION: Response JSON violates requested schema: x", True),
    ("JSON_PARSE_ERROR: Response is not valid JSON", True),
    ("UNSUPPORTED_ENDPOINT: /v1/completions is not served", True),
    ("Timeout after 300s for prompt p", False),
    ("HTTP 503 for prompt p: overloaded", False),
    ("HTTP 429 for prompt p: slow down", False),
    ("Client error '429 Too Many Requests' for url 'http://x'", False),
    ("Client error '408 Request Timeout' for url 'http://x'", False),
    ("INTERNAL_ERROR: ConnectError: connection refused", False),
    ("Unexpected error for prompt p: All connection attempts failed", False),
    ("EMBEDDING_FAILED: [Errno 111] Connection refused", False),
])
def test_only_deterministic_failures_are_final(error, final):
    assert is_final(CompletionResult(custom_id="p", error=error)) is final


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

    # The backend has the results now, so the journal is gone.
    assert not path.exists()


@pytest.mark.asyncio
async def test_deterministic_failures_are_kept_transient_ones_retried(tmp_path):
    errors = {
        "prompt-1": "HTTP 400 for prompt prompt-1: bad request",
        "prompt-2": "SCHEMA_VIOLATION: Response JSON violates requested schema: x",
        "prompt-3": "Client error '422 Unprocessable Entity' for url 'http://x'",
        "prompt-4": "Timeout after 300s for prompt prompt-4",
        "prompt-5": "HTTP 503 for prompt prompt-5: overloaded",
        "prompt-6": "Client error '429 Too Many Requests' for url 'http://x'",
        "prompt-7": "INTERNAL_ERROR: ConnectError: connection refused",
        "prompt-8": "[Errno 111] Connection refused",
    }
    prompts = make_prompts(9)                      # prompt-9 succeeds
    first = ErrorExecutor(errors.get)
    with pytest.raises(OSError):
        await _worker(tmp_path, first, UploadFailsClient(input_prompts=prompts))._execute_job(make_job())
    assert len(first.executed) == 9

    assert set(load(_journal_path(tmp_path))) == {"prompt-1", "prompt-2", "prompt-3", "prompt-9"}

    rerun = ErrorExecutor(lambda cid: None)        # runtime healthy again
    client = MockClient(input_prompts=prompts)
    await _worker(tmp_path, rerun, client)._execute_job(make_job())
    # Only the transient failures get another go; the deterministic ones
    # would fail identically, so they are not retried.
    assert sorted(rerun.executed) == ["prompt-4", "prompt-5", "prompt-6", "prompt-7", "prompt-8"]
    assert client.upload_calls[0]["completed"] == 6
    assert client.upload_calls[0]["failed"] == 3


@pytest.mark.asyncio
async def test_journal_is_keyed_on_the_input_rows_id(tmp_path):
    """An executor result echoing a different custom_id must not be restored
    into some other row."""
    class EchoingExecutor(SlowExecutor):
        async def execute(self, prompt):
            self.executed.append(prompt.custom_id)
            return _ok("prompt-2" if prompt.custom_id == "prompt-1" else prompt.custom_id)

    prompts = make_prompts(2)
    worker = _worker(tmp_path, EchoingExecutor(delay=0), UploadFailsClient(input_prompts=prompts),
                     concurrency=1)
    with pytest.raises(OSError):
        await worker._execute_job(make_job())

    assert set(load(_journal_path(tmp_path))) == {"prompt-1", "prompt-2"}


@pytest.mark.asyncio
async def test_journal_write_failure_does_not_lose_executed_work(tmp_path, monkeypatch):
    class BrokenFile:
        def write(self, data):
            raise OSError(28, "No space left on device")

        def flush(self):
            pass

        def fileno(self):
            raise OSError("closed")

        def close(self):
            pass

    real_open = ResultJournal.open

    def open_broken(path):
        journal = real_open(path)
        journal._fh.close()
        journal._fh = BrokenFile()
        return journal
    monkeypatch.setattr(ResultJournal, "open", staticmethod(open_broken))

    prompts = make_prompts(6)
    executor = SlowExecutor(delay=0)
    client = MockClient(input_prompts=prompts)
    await _worker(tmp_path, executor, client)._execute_job(make_job())

    assert len(executor.executed) == 6
    assert client.upload_calls[0]["completed"] == 6
    assert client.upload_calls[0]["failed"] == 0


@pytest.mark.asyncio
async def test_shutdown_placeholders_are_not_journalled(tmp_path):
    prompts = make_prompts(10)
    worker = _worker(tmp_path, SlowExecutor(delay=0.2), UploadFailsClient(input_prompts=prompts))

    task = asyncio.create_task(worker._execute_job(make_job()))
    await asyncio.sleep(0.05)
    worker._running = False
    with pytest.raises(OSError):
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


# ── Handing back a batch a dead process was holding ──────────────────


@pytest.mark.asyncio
async def test_leftover_marker_is_reported_once_then_cleared(tmp_path):
    (tmp_path / "batch-a").mkdir()
    (tmp_path / "batch-a" / IN_FLIGHT_MARKER).touch()
    (tmp_path / "batch-b").mkdir()                 # finished job: no marker

    client = ReclaimClient()
    worker = _worker(tmp_path, SlowExecutor(delay=0), client)
    await worker._poll_and_execute()
    await worker._poll_and_execute()

    assert [job_id for job_id, _ in client.failure_calls] == ["batch-a"]
    assert "worker restarted mid-job" in client.failure_calls[0][1]
    assert not (tmp_path / "batch-a" / IN_FLIGHT_MARKER).exists()


@pytest.mark.asyncio
async def test_undelivered_reclaim_is_retried_before_the_next_poll(tmp_path):
    (tmp_path / "batch-a").mkdir()
    (tmp_path / "batch-a" / IN_FLIGHT_MARKER).touch()

    client = ReclaimClient(delivered=False)
    worker = _worker(tmp_path, SlowExecutor(delay=0), client)
    await worker._poll_and_execute()
    assert (tmp_path / "batch-a" / IN_FLIGHT_MARKER).exists()

    client.delivered = True
    await worker._poll_and_execute()
    assert len(client.failure_calls) == 2
    assert not (tmp_path / "batch-a" / IN_FLIGHT_MARKER).exists()


@pytest.mark.asyncio
async def test_marker_and_journal_are_removed_when_a_job_finishes(tmp_path):
    prompts = make_prompts(3)
    client = ReclaimClient(input_prompts=prompts, job=make_job())
    await _worker(tmp_path, SlowExecutor(delay=0), client)._poll_and_execute()

    assert len(client.upload_calls) == 1
    assert not (tmp_path / "test-job" / IN_FLIGHT_MARKER).exists()
    assert not _journal_path(tmp_path).exists()


# ── kill -9 mid-batch ────────────────────────────────────────────────

_CHILD = textwrap.dedent('''
    import asyncio, sys
    sys.path.insert(0, sys.argv[1])
    from daemon.config import DaemonConfig
    from daemon.worker import Worker
    from tests.test_shutdown_failures import MockClient, SlowExecutor, make_job, make_prompts

    class Client(MockClient):
        async def poll_job(self):
            return make_job()

    async def main():
        config = DaemonConfig(work_dir=sys.argv[2], max_concurrent_prompts=2)
        client = Client(input_prompts=make_prompts(40))
        worker = Worker(config, client, {"ollama": SlowExecutor(delay=0.1)})
        worker._running = True
        await worker._poll_and_execute()   # the production entry point
        print("FINISHED", flush=True)      # must never be reached

    asyncio.run(main())
''')


@pytest.mark.asyncio
async def test_hard_kill_mid_batch_then_resume(tmp_path):
    """A process killed without any cleanup still leaves a resumable journal,
    and the restarted daemon hands the stranded batch back."""
    work_dir = tmp_path / "work"
    script = tmp_path / "child.py"
    script.write_text(_CHILD, encoding="utf-8")
    path = _journal_path(work_dir)

    proc = subprocess.Popen(
        [sys.executable, str(script), str(DAEMON_ROOT), str(work_dir)],
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
    assert (work_dir / "test-job" / IN_FLIGHT_MARKER).exists()

    done_before = set(load(path))
    assert 6 <= len(done_before) < 40

    # The restarted daemon hands the batch back before polling; the backend
    # requeues it, and here it comes straight back to this worker.
    prompts = make_prompts(40)
    rerun = SlowExecutor(delay=0)
    client = ReclaimClient(input_prompts=prompts, job=make_job())
    await _worker(work_dir, rerun, client)._poll_and_execute()

    assert [job_id for job_id, _ in client.failure_calls] == ["test-job"]

    # Completed prompts were skipped, not re-executed.
    assert not (set(rerun.executed) & done_before)
    assert len(rerun.executed) == 40 - len(done_before)

    ids = [r["custom_id"] for r in client.upload_calls[0]["lines"]]
    assert ids == [p.custom_id for p in prompts]   # each exactly once, in order
    assert client.upload_calls[0]["completed"] == 40
    assert client.progress_calls[-1] == (40, 0, 40)


# ── report_failure says whether the backend got the report ───────────


@pytest.mark.asyncio
@pytest.mark.parametrize("status,delivered", [(200, True), (403, True), (409, True), (503, False)])
async def test_report_failure_tells_delivered_from_undelivered(status, delivered):
    import httpx
    from daemon.client import BackendClient

    client = BackendClient("http://backend.test", worker_id="w-1")
    client._client = httpx.AsyncClient(
        base_url="http://backend.test",
        transport=httpx.MockTransport(lambda request: httpx.Response(status)),
    )
    try:
        assert await client.report_failure("batch-a", "boom") is delivered
    finally:
        await client._client.aclose()


@pytest.mark.asyncio
async def test_report_failure_unreachable_backend_is_undelivered():
    import httpx
    from daemon.client import BackendClient

    def refuse(request):
        raise httpx.ConnectError("connection refused", request=request)

    client = BackendClient("http://backend.test", worker_id="w-1")
    client._client = httpx.AsyncClient(
        base_url="http://backend.test", transport=httpx.MockTransport(refuse),
    )
    try:
        assert await client.report_failure("batch-a", "boom") is False
    finally:
        await client._client.aclose()
