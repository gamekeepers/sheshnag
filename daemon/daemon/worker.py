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
import random
import signal
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

        while self._running:
            try:
                await self._poll_and_execute()
            except asyncio.CancelledError:
                logger.info("Main loop cancelled")
                break
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

                    # Apply job-level defaults if not in prompt body
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
        Execute all prompts sequentially through the executor.

        Week 1: simple sequential execution.
        Week 2+: this is where batching / checkpointing would plug in.
        """
        results: List[CompletionResult] = []
        total = len(prompts)
        completed = 0
        failed = 0

        for idx, prompt in enumerate(prompts, start=1):
            if not self._running:
                logger.warning(
                    f"[{job.job_id}] Shutdown requested — "
                    f"stopping at prompt {idx}/{total}"
                )
                break

            logger.info(
                f"[{job.job_id}] Executing prompt {idx}/{total} "
                f"(id: {prompt.custom_id})"
            )

            result = await self._executor.execute(prompt)
            results.append(result)

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
                
            # Update heartbeat with current progress
            self._heartbeat.update_status(
                status="busy",
                job_id=job.job_id,
                progress={
                    "total_prompts": total,
                    "completed_prompts": completed,
                    "failed_prompts": failed,
                }
            )
            
            # Report progress to platform every 10 prompts or on completion
            if idx % 10 == 0 or idx == total:
                await self._client.report_progress(
                    job_id=job.job_id,
                    completed=completed,
                    failed=failed,
                    total=total,
                )

        return results

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
            loop.add_signal_handler(sig, self._handle_shutdown_signal, sig)

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
