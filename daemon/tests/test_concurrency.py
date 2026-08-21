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

    async def execute(self, prompt: PromptRequest) -> CompletionResult:
        self.calls.append(prompt.custom_id)
        if self.delay > 0:
            await asyncio.sleep(self.delay)
        return CompletionResult(
            custom_id=prompt.custom_id,
            response={"choices": [{"message": {"content": f"mock {prompt.custom_id}"}}]},
        )

    async def health_check(self) -> bool:
        return True


class OutOfOrderExecutor(BaseExecutor):
    def __init__(self):
        self.delay = 0.5
        self.calls = []

    async def execute(self, prompt: PromptRequest) -> CompletionResult:
        self.calls.append(prompt.custom_id)
        # Reverse order completion logic based on custom_id number (e.g. "prompt-1", "prompt-2")
        # Simulating out of order by sleeping longer for earlier prompts
        try:
            num = int(prompt.custom_id.split("-")[-1])
            await asyncio.sleep((10 - num) * 0.05)
        except ValueError:
            await asyncio.sleep(0.1)

        return CompletionResult(
            custom_id=prompt.custom_id,
            response={"choices": [{"message": {"content": f"out of order {prompt.custom_id}"}}]},
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


def make_prompts(count: int, endpoint: str = "/v1/chat/completions") -> List[PromptRequest]:
    prompts = []
    for i in range(1, count + 1):
        prompts.append(
            PromptRequest(
                custom_id=f"prompt-{i}",
                method="POST",
                url=endpoint,
                body={"model": "test-model", "messages": [{"role": "user", "content": f"hello {i}"}]},
            )
        )
    return prompts


@pytest.mark.asyncio
async def test_concurrency_throughput(mock_job):
    """Test that max_concurrent_prompts effectively parallelizes execution."""
    config = DaemonConfig(max_concurrent_prompts=8)
    executor = MockExecutor(delay=0.1)
    client = MockClient()
    worker = Worker(config, client, executor)
    
    prompts = make_prompts(32)
    worker._running = True

    start_time = time.monotonic()
    results = await worker._run_prompts(prompts, mock_job)
    end_time = time.monotonic()
    
    duration = end_time - start_time
    
    # 32 prompts with concurrency 8 and 0.1s delay should take roughly 0.4s (4 batches of 8)
    # Sequentially it would take 3.2s
    assert duration < 1.0, f"Execution too slow ({duration}s), concurrency not working"
    assert len(results) == 32
    assert len(executor.calls) == 32


@pytest.mark.asyncio
async def test_out_of_order_correctness(mock_job):
    """Test that out-of-order completion returns results in the original input order."""
    config = DaemonConfig(max_concurrent_prompts=10)
    executor = OutOfOrderExecutor()
    client = MockClient()
    worker = Worker(config, client, executor)
    
    prompts = make_prompts(10)
    worker._running = True

    results = await worker._run_prompts(prompts, mock_job)
    
    assert len(results) == 10
    # Even if they finished out of order, the return array should match input order
    for i, res in enumerate(results, start=1):
        assert res.custom_id == f"prompt-{i}"


@pytest.mark.asyncio
async def test_duplicate_custom_id_rejection(mock_job):
    config = DaemonConfig(max_concurrent_prompts=1)
    executor = MockExecutor()
    client = MockClient()
    worker = Worker(config, client, executor)
    
    prompts = make_prompts(2)
    prompts[1].custom_id = prompts[0].custom_id  # Create duplicate
    
    with pytest.raises(ValueError, match="Duplicate custom_id"):
        await worker._run_prompts(prompts, mock_job)


@pytest.mark.asyncio
async def test_graceful_shutdown(mock_job):
    config = DaemonConfig(max_concurrent_prompts=4)
    
    class SlowExecutor(BaseExecutor):
        async def execute(self, prompt: PromptRequest) -> CompletionResult:
            await asyncio.sleep(0.5)
            return CompletionResult(custom_id=prompt.custom_id, response={"done": True})
        async def health_check(self) -> bool: return True
        
    executor = SlowExecutor()
    client = MockClient()
    worker = Worker(config, client, executor)
    
    prompts = make_prompts(20)
    worker._running = True
    
    # Start task, then kill it shortly after
    task = asyncio.create_task(worker._run_prompts(prompts, mock_job))
    await asyncio.sleep(0.1)
    worker._running = False
    
    results = await task
    
    # It should have started some, but definitely not finished all 20
    assert len(results) < 20
    # And there shouldn't be any "half-executed" hanging prompts in the output queue that were aborted mid-sleep
    assert all(r.is_success for r in results)


@pytest.mark.asyncio
async def test_progress_reporting(mock_job):
    config = DaemonConfig(max_concurrent_prompts=4)
    executor = MockExecutor()
    client = MockClient()
    worker = Worker(config, client, executor)
    
    prompts = make_prompts(25)
    worker._running = True
    
    await worker._run_prompts(prompts, mock_job)
    
    assert len(client.progress_calls) >= 2
    last_call = client.progress_calls[-1]
    assert last_call == (25, 0, 25)  # 25 completed, 0 failed, 25 total
    
    # Should report at increments (10, 20, 25) or similar based on completion
    # Because of concurrency, exactly at 10 isn't strictly guaranteed depending on lock timing,
    # but there should be intermediate reports.
    assert len(client.progress_calls) >= 3


class MockBatchExecutor(BaseExecutor):
    def __init__(self):
        self.batch_calls = 0

    async def execute(self, prompt: PromptRequest) -> CompletionResult:
        raise NotImplementedError()

    async def batch_execute(self, prompts: List[PromptRequest]) -> List[CompletionResult]:
        self.batch_calls += 1
        return [CompletionResult(custom_id=p.custom_id, response={"emb": True}) for p in prompts]

    async def health_check(self) -> bool:
        return True


@pytest.mark.asyncio
async def test_batched_embeddings(mock_job):
    config = DaemonConfig(max_concurrent_prompts=8)
    executor = MockBatchExecutor()
    client = MockClient()
    worker = Worker(config, client, executor)
    
    prompts = make_prompts(64, endpoint="/v1/embeddings")
    worker._running = True
    
    results = await worker._run_prompts(prompts, mock_job)
    
    assert len(results) == 64
    assert executor.batch_calls == 1
    assert results[0].response == {"emb": True}

class MockCapacityExecutor(BaseExecutor):
    def __init__(self, limit: int | None = None):
        self.limit = limit
        self.query_called = False

    async def execute(self, prompt: PromptRequest) -> CompletionResult:
        return CompletionResult(custom_id=prompt.custom_id, response={"ok": True})
        
    async def health_check(self) -> bool:
        return True
        
    async def query_concurrency_limit(self) -> int | None:
        self.query_called = True
        return self.limit

@pytest.mark.asyncio
async def test_worker_auto_detects_capacity(mock_job):
    config = DaemonConfig(max_concurrent_prompts=8, concurrency_explicit=False)
    executor = MockCapacityExecutor(limit=16)
    client = MockClient()
    worker = Worker(config, client, executor)
    
    # We just want to test start() up to the pool_and_execute loop
    worker._poll_and_execute = lambda: asyncio.sleep(0.1)
    
    task = asyncio.create_task(worker.start())
    await asyncio.sleep(0.05)
    worker._running = False
    await task
    
    assert executor.query_called
    # 75% of 16 = 12
    assert worker._concurrency_controller.current_concurrency == 12
    assert worker._concurrency_controller.max_concurrency == 16

@pytest.mark.asyncio
async def test_worker_fallback_on_query_failure(mock_job):
    config = DaemonConfig(max_concurrent_prompts=8, concurrency_explicit=False)
    executor = MockCapacityExecutor(limit=None)
    client = MockClient()
    worker = Worker(config, client, executor)
    
    worker._poll_and_execute = lambda: asyncio.sleep(0.1)
    
    task = asyncio.create_task(worker.start())
    await asyncio.sleep(0.05)
    worker._running = False
    await task
    
    assert executor.query_called
    # Default is 8, max is 128
    assert worker._concurrency_controller.current_concurrency == 8
    assert worker._concurrency_controller.max_concurrency == 128

@pytest.mark.asyncio
async def test_worker_respects_explicit_override(mock_job):
    config = DaemonConfig(max_concurrent_prompts=4, concurrency_explicit=True)
    executor = MockCapacityExecutor(limit=32)
    client = MockClient()
    worker = Worker(config, client, executor)
    
    worker._poll_and_execute = lambda: asyncio.sleep(0.1)
    
    task = asyncio.create_task(worker.start())
    await asyncio.sleep(0.05)
    worker._running = False
    await task
    
    # It shouldn't even query if it's explicitly set
    assert not executor.query_called
    assert worker._concurrency_controller.current_concurrency == 4
