"""
Worker — the core orchestration loop of the daemon.

The Worker ties together the BackendClient and BaseExecutor:
    1. Poll the backend for a job
    2. Download the input JSONL
    3. Execute each prompt via the executor
    4. Write the output JSONL
    5. Upload results to the backend
    6. Repeat

Design decisions:
    - Worker owns the main loop but delegates all I/O
    - Graceful shutdown via signal handlers (SIGTERM, SIGINT)
    - Job artifacts are stored in per-job directories under work_dir
    - Errors in individual prompts don't fail the whole job
    - Worker never raises from the main loop (log + continue)
    - Poll jitter prevents thundering herd when many workers poll

Dependencies are injected via constructor — this is key for testing
and for the Open/Closed Principle (swap executor without touching Worker).
"""

from __future__ import annotations

import asyncio
import json
import math
import random
import signal
import time
from pathlib import Path
from typing import List

import httpx

from daemon.client import BackendClient
from daemon.config import DaemonConfig
from daemon.executors.base import BaseExecutor
from daemon.executors.ollama import OllamaExecutor
from daemon.log import get_logger
from daemon.models import CompletionResult, Job, PromptRequest
from daemon.heartbeat import HeartbeatManager
from daemon.model_manager import ModelManager
from daemon.concurrency import AIMDConcurrencyController

logger = get_logger(__name__)


class Worker:
    """
    Main daemon worker — polls for jobs and executes them.

    The Worker is the only component that knows about the full workflow.
    It coordinates between the client (backend HTTP) and executor
    (inference runtime) but doesn't implement either.

    Args:
        config:   Daemon configuration.
        client:   HTTP client for backend communication.
        executor: Inference executor (e.g., VLLMExecutor).
    """

    def __init__(
        self,
        config: DaemonConfig,
        client: BackendClient,
        executor: BaseExecutor,
    ) -> None:
        self._config = config
        self._client = client
        self._executor = executor
        self._running = False
        self._current_job_id: str | None = None
        self._concurrency_controller = AIMDConcurrencyController(
            initial_concurrency=config.max_concurrent_prompts,
            max_concurrency=128
        )

        self._heartbeat = HeartbeatManager(
            client=client,
            worker_id=config.worker_id,
            interval=config.heartbeat_interval,
            get_loaded_models=self._get_loaded_models,
            get_loaded_model_digests=self._get_loaded_model_digests,
        )

        self._model_manager = None
        if isinstance(executor, OllamaExecutor):
            self._model_manager = ModelManager(
                executor=executor,
                client=client,
                worker_id=config.worker_id,
            )

        # Ensure work directory exists
        self._work_dir = Path(config.work_dir)
        self._work_dir.mkdir(parents=True, exist_ok=True)

    async def _get_loaded_models(self) -> List[str]:
        """
        Models currently served by the runtime, reported in heartbeats
        so the scheduler can prefer workers that already host a model.
        Falls back to the statically configured list for runtimes that
        can't be queried.
        """
        if hasattr(self._executor, "list_models"):
            return await self._executor.list_models()
        return list(self._config.models)

    async def _get_loaded_model_digests(self) -> dict:
        """name → digest map for loaded models (reproducibility pins).

        Empty for runtimes that can't report digests; the backend then
        falls back to name matching.
        """
        if hasattr(self._executor, "list_models_detailed"):
            return {
                m["name"]: m.get("digest")
                for m in await self._executor.list_models_detailed()
                if m.get("name")
            }
        return {}

    # ── Public API ───────────────────────────────────────────────

    async def start(self) -> None:
        """
        Start the main poll-execute loop.

        This method blocks until a shutdown signal is received or
        an unrecoverable error occurs. It is designed to be called
        from asyncio.run() in main.py.
        """
        self._running = True
        self._install_signal_handlers()

        logger.info(
            f"Worker '{self._config.worker_id}' started — "
            f"polling {self._config.backend_url} "
            f"every {self._config.poll_interval}s"
        )
        
        await self._heartbeat.start()

        # Pre-flight: check vLLM health
        await self._wait_for_executor()

        # Query runtime for concurrency limit
        if not self._config.concurrency_explicit:
            detected_limit = await self._executor.query_concurrency_limit()
            if detected_limit is not None:
                initial = max(1, math.floor(detected_limit * 0.75))
                maximum = max(initial, detected_limit)
                self._concurrency_controller.reset(initial, maximum)
                logger.info(
                    f"Runtime capacity detected: {detected_limit}, "
                    f"AIMD starting at {initial} (max {maximum})"
                )
            else:
                logger.info("Could not detect runtime capacity, using default concurrency")
        else:
            logger.info(f"Using explicitly configured concurrency: {self._config.max_concurrent_prompts}")

        while self._running:
            try:
                await self._poll_and_execute()
            except asyncio.CancelledError:
                logger.info("Main loop cancelled")
                break
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 401:
                    # API key revoked or expired — non-recoverable, shut down
                    logger.error(
                        f"HTTP 401 from backend — API key likely revoked. Shutting down."
                    )
                    break
                logger.error(f"Recoverable HTTP error in main loop: {exc}", exc_info=True)
            except (httpx.HTTPError, OSError, ValueError, json.JSONDecodeError) as exc:
                # Catch only recoverable errors — let fatal exceptions
                # (MemoryError, SystemExit, KeyboardInterrupt) propagate
                # so the process can terminate properly.
                logger.error(f"Recoverable error in main loop: {exc}", exc_info=True)
            except Exception as exc:
                # Catch remaining non-fatal exceptions with a warning
                # that this catch-all should ideally be narrowed further.
                logger.error(
                    f"Unexpected error in main loop (consider narrowing this catch): {exc}",
                    exc_info=True,
                )

            if self._running:
                # Add ±20% jitter to prevent thundering herd when
                # many workers poll the same backend simultaneously.
                jitter = self._config.poll_interval * random.uniform(0.8, 1.2)
                await asyncio.sleep(jitter)

        logger.info("Worker main loop exited")

    async def shutdown(self) -> None:
        """
        Graceful shutdown — close all connections and resources.

        Safe to call multiple times.
        """
        self._running = False
        logger.info("Shutting down worker...")

        await self._heartbeat.stop()
        await self._executor.close()
        await self._client.close()

        logger.info("Worker shutdown complete")

    # ── Main Loop Logic ──────────────────────────────────────────

    async def _poll_and_execute(self) -> None:
        """Single iteration of the poll-execute cycle."""
        logger.debug("Polling for jobs...")
        job = await self._client.poll_job()

        if job is None:
            logger.debug("No jobs available — will retry")
            return

        self._current_job_id = job.job_id
        logger.info(f"{'='*60}")
        logger.info(f"JOB STARTED: {job.job_id}")
        logger.info(f"{'='*60}")

        try:
            await self._execute_job(job)
        except Exception as exc:
            logger.error(
                f"Job {job.job_id} failed with error: {exc}",
                exc_info=True,
            )
            # Report failure to backend so it can requeue the job
            # (spec §11: worker reports failure)
            await self._client.report_failure(job.job_id, str(exc))
        finally:
            self._current_job_id = None

    async def _execute_job(self, job: Job) -> None:
        """
        Full lifecycle of a single job:
            download → parse → execute → write → upload
        """
        job_dir = self._work_dir / job.job_id
        job_dir.mkdir(parents=True, exist_ok=True)

        input_path = job_dir / "input.jsonl"
        output_path = job_dir / "output.jsonl"
        
        # ── Step 0: Ensure model is available ────────────────────────
        if self._model_manager and job.model:
            self._heartbeat.update_status("downloading_model", job.job_id)
            available = await self._model_manager.ensure_model(job.model)
            if not available:
                await self._client.report_failure(job.job_id, f"Failed to download model: {job.model}")
                self._heartbeat.update_status("idle")
                return

        # ── Step 1: Download input ───────────────────────────────
        self._heartbeat.update_status("busy", job.job_id)
        logger.info(f"[{job.job_id}] Downloading input file...")
        await self._client.download_input(job.job_id, job.input_path, input_path)

        # ── Step 2: Parse prompts ────────────────────────────────
        prompts = self._parse_input(input_path, job)
        total = len(prompts)
        logger.info(f"[{job.job_id}] Parsed {total} prompts from input")

        if total == 0:
            # Don't silently return — the job would stay in "running"
            # state forever. Report failure so the backend can handle it.
            logger.warning(f"[{job.job_id}] Input file is empty — reporting failure")
            await self._client.report_failure(
                job.job_id, "Input file is empty — no prompts to process"
            )
            return

        # ── Step 3: Execute each prompt ──────────────────────────
        results = await self._run_prompts(prompts, job)

        # ── Step 4: Write output JSONL ───────────────────────────
        self._write_output(output_path, results)

        # ── Step 5: Upload results (with real counts) ────────────
        successes = sum(1 for r in results if r.is_success)
        failures = total - successes

        logger.info(f"[{job.job_id}] Uploading results...")
        await self._client.upload_results(
            job.job_id, output_path, completed=successes, failed=failures
        )

        # ── Summary ──────────────────────────────────────────────
        total_tokens = sum(
            r.usage.get("total_tokens", 0) for r in results
        )

        logger.info(f"[{job.job_id}] Job completed!")
        logger.info(
            f"[{job.job_id}] Results: {successes}/{total} succeeded, "
            f"{failures} failed, {total_tokens:,} total tokens"
        )
        
        self._heartbeat.update_status("idle")

    # ── Prompt Processing ────────────────────────────────────────

    def _parse_input(self, input_path: Path, job: Job) -> List[PromptRequest]:
        """
        Parse input JSONL into a list of PromptRequest objects.

        Applies job-level defaults (max_tokens, temperature) to prompts
        that don't specify their own values, and forces the runtime model
        id resolved by the backend.
        """
        prompts: List[PromptRequest] = []

        with open(input_path, "r") as fh:
            for line_num, line in enumerate(fh, start=1):
                line = line.strip()
                if not line:
                    continue

                try:
                    raw = json.loads(line)
                    prompt = PromptRequest(**raw)

                    # Apply job-level defaults if not in prompt body (only for non-embedding requests)
                    if prompt.url != "/v1/embeddings":
                        prompt.body.setdefault("max_tokens", job.max_tokens)
                        prompt.body.setdefault("temperature", job.temperature)

                    # body.model is the platform catalogue id the user
                    # submitted; run the runtime id the backend resolved
                    # (job.model = runtime_model_id from poll), else the
                    # runtime 404s on an unknown model name.
                    if job.model:
                        prompt.body["model"] = job.model

                    prompts.append(prompt)
                except (json.JSONDecodeError, Exception) as exc:
                    logger.warning(
                        f"[{job.job_id}] Skipping malformed line {line_num}: {exc}"
                    )

        return prompts

    async def _run_prompts(
        self, prompts: List[PromptRequest], job: Job
    ) -> List[CompletionResult]:
        """
        Execute all prompts concurrently using a bounded task pool.
        """
        # 1. Duplicate custom_id detection (pre-flight)
        seen_ids = set()
        for p in prompts:
            if p.custom_id in seen_ids:
                logger.error(f"[{job.job_id}] Duplicate custom_id '{p.custom_id}' in input")
                raise ValueError(f"Duplicate custom_id '{p.custom_id}' in input")
            seen_ids.add(p.custom_id)

        # 2. Set up shared state
        total = len(prompts)
        queue = asyncio.Queue()
        results_by_id: dict[str, CompletionResult] = {}
        lock = asyncio.Lock()
        completed = 0
        failed = 0

        chat_prompts = []
        embedding_prompts = []
        for p in prompts:
            if p.url == "/v1/embeddings":
                embedding_prompts.append(p)
            else:
                chat_prompts.append(p)
                queue.put_nowait(p)

        # 3. Worker coroutine
        # This function processes items from the queue until it's empty or shutdown is requested.
        # It's spawned dynamically by the pool_manager below.
        active_workers = set()

        async def pool_worker(task_ref=None):
            if task_ref is None:
                task_ref = asyncio.current_task()
            
            try:
                nonlocal completed, failed
                while True:
                    # Graceful shutdown check between each prompt execution
                    if not self._running:
                        return
                    
                    # AIMD Scaling down logic:
                    # If the controller detected network strain (e.g. 429 Too Many Requests)
                    # and decreased current_concurrency, excess workers will exit here naturally
                    # before picking up the next prompt from the queue.
                    if len(active_workers) > self._concurrency_controller.current_concurrency:
                        return
                        
                    try:
                        # Non-blocking get. The manager ensures queue has items when workers are spawned.
                        prompt = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        return

                    logger.info(
                        f"[{job.job_id}] Executing prompt "
                        f"(id: {prompt.custom_id})"
                    )

                    # Pre-executor validation
                    if prompt.body.get("stream"):
                        result = CompletionResult(
                            custom_id=prompt.custom_id,
                            error=(
                                "UNSUPPORTED_PARAMETER: stream=true is not "
                                "supported in batch mode. Batch requests are "
                                "executed synchronously and the streaming "
                                "response shape is incompatible."
                            ),
                        )
                    else:
                        start_time = time.monotonic()
                        result = await self._executor.execute(prompt)
                        latency = time.monotonic() - start_time
                        
                        if result.is_success:
                            self._concurrency_controller.on_success(latency)
                        else:
                            self._concurrency_controller.on_failure(result.error)

                    should_report = False
                    report_completed = 0
                    report_failed = 0

                    # Thread-safe (async-safe) state updates since multiple workers run concurrently.
                    async with lock:
                        results_by_id[prompt.custom_id] = result
                        if result.is_success:
                            completed += 1
                            tokens = result.usage.get("total_tokens", "?")
                            logger.debug(
                                f"[{job.job_id}] Prompt {prompt.custom_id} "
                                f"completed ({tokens} tokens)"
                            )
                        else:
                            failed += 1
                            logger.warning(
                                f"[{job.job_id}] Prompt {prompt.custom_id} "
                                f"failed: {result.error}"
                            )
    
                        done = completed + failed
                        # Report to the backend periodically (every 10 prompts or at the end)
                        if done % 10 == 0 or done == total:
                            should_report = True
                            report_completed = completed
                            report_failed = failed
    
                        # Update heartbeat
                        self._heartbeat.update_status(
                            status="busy",
                            job_id=job.job_id,
                            progress={
                                "total_prompts": total,
                                "completed_prompts": completed,
                                "failed_prompts": failed,
                            }
                        )
    
                    if should_report:
                        await self._client.report_progress(
                            job_id=job.job_id,
                            completed=report_completed,
                            failed=report_failed,
                            total=total,
                        )
            finally:
                active_workers.discard(task_ref)

        # 4. Launch dynamic pool manager and wait
        # The pool_manager constantly polls the AIMD concurrency limit and spawns new tasks
        # to fill available capacity if the queue still has pending prompts.
        async def pool_manager():
            while self._running and not queue.empty():
                target = self._concurrency_controller.current_concurrency
                
                # We update active_workers synchronously immediately after spawning
                # to prevent the manager from spinning in an infinite loop while waiting 
                # for the spawned coroutine to actually execute its first line.
                while len(active_workers) < target and not queue.empty():
                    task = asyncio.create_task(pool_worker())
                    active_workers.add(task)
                    
                # Poll interval. Short enough to scale up fast, long enough to save CPU.
                await asyncio.sleep(0.5)
                
        manager_task = asyncio.create_task(pool_manager())
        
        # Wait until the job queue is fully depleted by the active workers
        # Checking self._running breaks the wait immediately during graceful shutdowns.
        while self._running and not queue.empty():
            await asyncio.sleep(0.5)
            
        # Ensure the manager loop exits cleanly and await all currently executing workers
        # so we don't accidentally orphan running HTTP tasks.
        manager_task.cancel()
        if active_workers:
            res_list = await asyncio.gather(*active_workers, return_exceptions=True)
            for res in res_list:
                if isinstance(res, Exception):
                    logger.error(f"Worker crashed with exception: {res}")
                    raise res

        # 5. Handle embeddings
        if embedding_prompts and self._running:
            logger.info(f"[{job.job_id}] Executing {len(embedding_prompts)} embeddings via batch_execute")
            try:
                emb_results = await self._executor.batch_execute(embedding_prompts)
                
                should_report = False
                report_completed = 0
                report_failed = 0

                async with lock:
                    for r in emb_results:
                        results_by_id[r.custom_id] = r
                        if r.is_success:
                            completed += 1
                        else:
                            failed += 1

                    should_report = True
                    report_completed = completed
                    report_failed = failed

                    self._heartbeat.update_status(
                        status="busy",
                        job_id=job.job_id,
                        progress={
                            "total_prompts": total,
                            "completed_prompts": completed,
                            "failed_prompts": failed,
                        }
                    )

                if should_report:
                    await self._client.report_progress(
                        job_id=job.job_id,
                        completed=report_completed,
                        failed=report_failed,
                        total=total,
                    )
            except Exception as exc:
                logger.error(f"[{job.job_id}] Batch embedding execution failed: {exc}")
                async with lock:
                    for p in embedding_prompts:
                        results_by_id[p.custom_id] = CompletionResult(
                            custom_id=p.custom_id, error=str(exc)
                        )
                        failed += 1
                        
                    self._heartbeat.update_status(
                        status="busy",
                        job_id=job.job_id,
                        progress={
                            "total_prompts": total,
                            "completed_prompts": completed,
                            "failed_prompts": failed,
                        }
                    )
                
                await self._client.report_progress(
                    job_id=job.job_id,
                    completed=completed,
                    failed=failed,
                    total=total,
                )

        if not self._running:
            logger.warning(
                f"[{job.job_id}] Shutdown requested — "
                f"stopping early"
            )

        # 6. Return results in input order (only what completed)
        final_results = []
        for p in prompts:
            if p.custom_id in results_by_id:
                final_results.append(results_by_id[p.custom_id])
                
        return final_results

    # ── Output Writing ───────────────────────────────────────────

    def _write_output(
        self, output_path: Path, results: List[CompletionResult]
    ) -> None:
        """Write results to output JSONL file."""
        with open(output_path, "w") as fh:
            for result in results:
                fh.write(result.model_dump_json() + "\n")

        file_size = output_path.stat().st_size
        logger.info(
            f"Wrote {len(results)} results to {output_path} "
            f"({file_size:,} bytes)"
        )

    # ── Executor Health ──────────────────────────────────────────

    async def _wait_for_executor(self) -> None:
        """
        Wait for the inference executor to become healthy.

        Retries with backoff on startup. This handles the case where
        the daemon starts before vLLM is fully loaded.
        """
        max_retries = 12  # 12 * 5s = 60s max wait
        for attempt in range(1, max_retries + 1):
            if await self._executor.health_check():
                logger.info("Executor health check passed ✓")
                return

            logger.warning(
                f"Executor not ready — retry {attempt}/{max_retries} "
                f"in 5s..."
            )
            await asyncio.sleep(5)

        # Don't crash — proceed anyway, individual prompts will fail
        # with descriptive errors if the executor is truly down
        logger.error(
            "Executor health check failed after all retries — "
            "proceeding anyway (prompts may fail)"
        )

    # ── Signal Handling ──────────────────────────────────────────

    def _install_signal_handlers(self) -> None:
        """Register OS signal handlers for graceful shutdown."""
        loop = asyncio.get_running_loop()

        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self._handle_shutdown_signal, sig)
            except NotImplementedError:
                # add_signal_handler is not supported on Windows event loops.
                # Graceful shutdown via signals will not work, but KeyboardInterrupt
                # will still be raised on Ctrl+C.
                pass

    def _handle_shutdown_signal(self, sig: signal.Signals) -> None:
        """Handle SIGTERM/SIGINT by requesting graceful shutdown."""
        sig_name = signal.Signals(sig).name
        logger.info(f"Received {sig_name} — initiating graceful shutdown")

        if self._current_job_id:
            logger.info(
                f"Currently processing job {self._current_job_id} — "
                f"will stop after current prompt"
            )

        self._running = False
