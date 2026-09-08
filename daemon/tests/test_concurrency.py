"""
Tests for the bounded concurrency pool in Worker._run_prompts (issue #55).

Adaptive sizing of the pool is issue #101 and is not covered here — these
tests all pin concurrency to an explicit config value.
"""

import asyncio
import time
from typing import List

import pytest

from daemon.config import DaemonConfig
from daemon.executors.base import BaseExecutor
from daemon.models import CompletionResult, PromptRequest, Job
from daemon.worker import Worker


class MockExecutor(BaseExecutor):
    def __init__(self, delay: float = 0.0):
        self.delay = delay
        self.calls = []
        self.max_in_flight = 0
        self._in_flight = 0

    async def execute(self, prompt: PromptRequest) -> CompletionResult:
        self.calls.append(prompt.custom_id)
        self._in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self._in_flight)
        try:
            if self.delay > 0:
                await asyncio.sleep(self.delay)
            return CompletionResult(
                custom_id=prompt.custom_id,
                response={
                    "choices": [
                        {"message": {"content": f"mock {prompt.custom_id}"}}
                    ]
                },
            )
        finally:
            self._in_flight -= 1

    async def health_check(self) -> bool:
        return True


class OutOfOrderExecutor(BaseExecutor):
    """Finishes later prompts first, to prove results are re-ordered."""

    def __init__(self):
        self.calls = []

    async def execute(self, prompt: PromptRequest) -> CompletionResult:
        self.calls.append(prompt.custom_id)
        num = int(prompt.custom_id.split("-")[-1])
        await asyncio.sleep((10 - num) * 0.05)
        return CompletionResult(
            custom_id=prompt.custom_id,
            response={
                "choices": [
                    {"message": {"content": f"out of order {prompt.custom_id}"}}
                ]
            },
        )

    async def health_check(self) -> bool:
        return True


class MockClient:
    def __init__(self):
        self.progress_calls = []

    async def report_progress(self, job_id, completed, failed, total):
        self.progress_calls.append((completed, failed, total))

    async def update_worker_id(self, worker_id):
        pass


@pytest.fixture
def mock_job():
    return Job(
        job_id="test-job-1",
        input_file_id="in-1",
        output_file_id="out-1",
        model="test-model",
        status="running",
    )


def make_prompts(
    count: int, endpoint: str = "/v1/chat/completions"
) -> List[PromptRequest]:
    return [
        PromptRequest(
            custom_id=f"prompt-{i}",
            method="POST",
            url=endpoint,
            body={
                "model": "test-model",
                "messages": [{"role": "user", "content": f"hello {i}"}],
            },
        )
        for i in range(1, count + 1)
    ]


def _worker(executor, client, **cfg):
    config = DaemonConfig(**cfg)
    worker = Worker(config, client, executor)
    worker._running = True
    return worker


@pytest.mark.asyncio
async def test_prompts_run_concurrently(mock_job):
    """32 prompts at concurrency 8 must beat sequential execution decisively."""
    executor = MockExecutor(delay=0.1)
    worker = _worker(executor, MockClient(), max_concurrent_prompts=8)

    start = time.monotonic()
    results = await worker._run_prompts(make_prompts(32), mock_job)
    duration = time.monotonic() - start

    # Sequential would be ~3.2s; 4 waves of 8 is ~0.4s.
    assert duration < 1.0, f"took {duration:.2f}s — pool is not parallelising"
    assert len(results) == 32
    assert len(executor.calls) == 32


@pytest.mark.asyncio
async def test_pool_size_is_capped_by_config(mock_job):
    """Concurrency must not exceed max_concurrent_prompts."""
    executor = MockExecutor(delay=0.05)
    worker = _worker(executor, MockClient(), max_concurrent_prompts=3)

    await worker._run_prompts(make_prompts(20), mock_job)

    assert executor.max_in_flight <= 3, (
        f"{executor.max_in_flight} prompts were in flight, cap was 3"
    )
    assert executor.max_in_flight == 3, "pool never reached its configured size"


@pytest.mark.asyncio
async def test_results_return_in_input_order(mock_job):
    """Out-of-order completion must still produce input-ordered output."""
    worker = _worker(OutOfOrderExecutor(), MockClient(), max_concurrent_prompts=10)

    results = await worker._run_prompts(make_prompts(10), mock_job)

    assert [r.custom_id for r in results] == [f"prompt-{i}" for i in range(1, 11)]


@pytest.mark.asyncio
async def test_duplicate_custom_id_fails_only_that_row(mock_job):
    """A duplicate id must not abort the job — the pre-PR loop never did."""
    worker = _worker(MockExecutor(), MockClient(), max_concurrent_prompts=4)
    prompts = make_prompts(3)
    prompts[2].custom_id = prompts[0].custom_id  # row 2 duplicates row 0

    results = await worker._run_prompts(prompts, mock_job)

    assert len(results) == 3, "every input row must still produce an output row"
    assert results[0].is_success
    assert results[1].is_success
    assert not results[2].is_success
    assert results[2].error.startswith("DUPLICATE_CUSTOM_ID:")


@pytest.mark.asyncio
async def test_stream_rejected_for_embeddings_too(mock_job):
    """stream=true is decided at the partition, so no path can miss it."""
    worker = _worker(MockBatchExecutor(), MockClient(), max_concurrent_prompts=4)
    prompts = make_prompts(2, endpoint="/v1/embeddings")
    prompts[0].body["stream"] = True

    results = await worker._run_prompts(prompts, mock_job)

    assert results[0].error.startswith("UNSUPPORTED_PARAMETER:")
    assert results[1].is_success


@pytest.mark.asyncio
async def test_one_crashing_prompt_does_not_lose_the_batch(mock_job):
    """An unexpected exception fails its own row and nothing else."""

    class PoisonExecutor(BaseExecutor):
        async def execute(self, prompt: PromptRequest) -> CompletionResult:
            if prompt.custom_id == "prompt-3":
                raise RuntimeError("boom")
            return CompletionResult(
                custom_id=prompt.custom_id, response={"choices": [{"message": {}}]}
            )

        async def health_check(self) -> bool:
            return True

    worker = _worker(PoisonExecutor(), MockClient(), max_concurrent_prompts=4)
    results = await worker._run_prompts(make_prompts(10), mock_job)

    assert len(results) == 10, "a crashing prompt destroyed completed results"
    assert sum(r.is_success for r in results) == 9
    bad = [r for r in results if not r.is_success]
    assert len(bad) == 1 and bad[0].custom_id == "prompt-3"
    assert bad[0].error.startswith("INTERNAL_ERROR:")


@pytest.mark.asyncio
async def test_null_usage_does_not_crash_the_worker(mock_job):
    """A runtime answering "usage": null must not kill the row or the job."""

    class NullUsageExecutor(BaseExecutor):
        async def execute(self, prompt: PromptRequest) -> CompletionResult:
            return CompletionResult(
                custom_id=prompt.custom_id,
                response={"choices": [{"message": {}}], "usage": None},
            )

        async def health_check(self) -> bool:
            return True

    worker = _worker(NullUsageExecutor(), MockClient(), max_concurrent_prompts=2)
    results = await worker._run_prompts(make_prompts(4), mock_job)

    assert len(results) == 4
    assert all(r.is_success for r in results)


@pytest.mark.asyncio
async def test_graceful_shutdown_drains_in_flight_work(mock_job):
    """Shutdown stops new work but lets in-flight prompts finish cleanly."""

    class SlowExecutor(BaseExecutor):
        async def execute(self, prompt: PromptRequest) -> CompletionResult:
            await asyncio.sleep(0.5)
            return CompletionResult(
                custom_id=prompt.custom_id, response={"done": True}
            )

        async def health_check(self) -> bool:
            return True

    worker = _worker(SlowExecutor(), MockClient(), max_concurrent_prompts=4)

    task = asyncio.create_task(worker._run_prompts(make_prompts(20), mock_job))
    await asyncio.sleep(0.1)
    worker._running = False
    results = await task

    assert len(results) < 20, "shutdown did not stop the pool"
    assert all(r.is_success for r in results), "a partial result leaked out"


@pytest.mark.asyncio
async def test_progress_is_reported_and_ends_complete(mock_job):
    """Progress is time-throttled (#87), but completion always reports."""
    client = MockClient()
    worker = _worker(
        MockExecutor(), client,
        max_concurrent_prompts=4,
        progress_interval_seconds=0.001,
    )

    await worker._run_prompts(make_prompts(25), mock_job)

    assert client.progress_calls, "no progress was reported"
    assert client.progress_calls[-1] == (25, 0, 25)


@pytest.mark.asyncio
async def test_progress_throttle_suppresses_chatter(mock_job):
    """A long interval collapses reporting to the opening and final calls."""
    client = MockClient()
    worker = _worker(
        MockExecutor(), client,
        max_concurrent_prompts=4,
        progress_interval_seconds=3600.0,
    )

    await worker._run_prompts(make_prompts(25), mock_job)

    assert len(client.progress_calls) <= 2
    assert client.progress_calls[-1] == (25, 0, 25)


class MockBatchExecutor(BaseExecutor):
    def __init__(self):
        self.batch_calls = 0
        self.max_in_flight = 0
        self._in_flight = 0

    async def execute(self, prompt: PromptRequest) -> CompletionResult:
        raise NotImplementedError("embeddings must go through batch_execute")

    async def batch_execute(
        self, prompts: List[PromptRequest]
    ) -> List[CompletionResult]:
        self.batch_calls += 1
        self._in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self._in_flight)
        try:
            await asyncio.sleep(0.02)
            return [
                CompletionResult(custom_id=p.custom_id, response={"emb": True})
                for p in prompts
            ]
        finally:
            self._in_flight -= 1

    async def health_check(self) -> bool:
        return True


@pytest.mark.asyncio
async def test_embeddings_coalesce_when_the_runtime_can(mock_job):
    """An executor declaring a chunk size gets one call per chunk."""
    executor = MockBatchExecutor()
    executor.embedding_chunk_size = 64
    worker = _worker(executor, MockClient(), max_concurrent_prompts=8)

    results = await worker._run_prompts(
        make_prompts(64, endpoint="/v1/embeddings"), mock_job
    )

    assert len(results) == 64
    assert executor.batch_calls == 1
    assert results[0].response == {"emb": True}


@pytest.mark.asyncio
async def test_embeddings_run_in_the_pool_when_it_cannot(mock_job):
    """Without coalescing, embeddings still get the pool — not a serial tail."""
    executor = MockBatchExecutor()  # inherits chunk size 1 from BaseExecutor
    worker = _worker(executor, MockClient(), max_concurrent_prompts=8)

    results = await worker._run_prompts(
        make_prompts(16, endpoint="/v1/embeddings"), mock_job
    )

    assert len(results) == 16
    assert executor.batch_calls == 16, "rows were not scheduled individually"
    assert executor.max_in_flight > 1, "embeddings ran serially, not in the pool"


@pytest.mark.asyncio
async def test_mixed_batch_keeps_every_row(mock_job):
    """A job mixing chat and embeddings returns one row per input, in order."""

    class MixedExecutor(BaseExecutor):
        async def execute(self, prompt: PromptRequest) -> CompletionResult:
            return CompletionResult(
                custom_id=prompt.custom_id,
                response={"choices": [{"message": {"content": "chat"}}]},
            )

        async def batch_execute(self, prompts):
            return [
                CompletionResult(custom_id=p.custom_id, response={"emb": True})
                for p in prompts
            ]

        async def health_check(self) -> bool:
            return True

    prompts = make_prompts(5)
    for p in prompts[1::2]:
        p.url = "/v1/embeddings"

    worker = _worker(MixedExecutor(), MockClient(), max_concurrent_prompts=4)
    results = await worker._run_prompts(prompts, mock_job)

    assert [r.custom_id for r in results] == [p.custom_id for p in prompts]
    assert results[0].response == {"choices": [{"message": {"content": "chat"}}]}
    assert results[1].response == {"emb": True}
