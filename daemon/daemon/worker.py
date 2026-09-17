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
import time
from pathlib import Path
from typing import Dict, List, Optional

import httpx

from daemon.client import BackendClient
from daemon.config import DaemonConfig
from daemon.executors.base import BaseExecutor
from daemon.executors.ollama import OllamaExecutor
from daemon.executor_factory import VLLM_ROCM_HINT
from daemon.hardware import gpu_vendors_present
from daemon.log import get_logger
from daemon.models import CompletionResult, Job, PromptRequest
from daemon.heartbeat import HeartbeatManager
from daemon.model_manager import ModelManager

logger = get_logger(__name__)

#: Startup readiness wait, per runtime: 12 × 5s = 60s max. Runtimes are
#: checked concurrently, so a mixed node waits 60s total, not 60s each.
READINESS_MAX_RETRIES = 12
READINESS_RETRY_DELAY = 5.0


class Worker:
    """
    Main daemon worker — polls for jobs and executes them.

    The Worker is the only component that knows about the full workflow.
    It coordinates between the client (backend HTTP) and the executors
    (inference runtimes) but doesn't implement either. A worker may
    drive several runtimes on one node (e.g. vLLM + Ollama); jobs are
    routed to the runtime that serves their model.

    Args:
        config:    Daemon configuration.
        client:    HTTP client for backend communication.
        executors: One executor per configured runtime, keyed by runtime
                   name (e.g. {"ollama": OllamaExecutor, "vllm": VLLMExecutor}).
    """

    def __init__(
        self,
        config: DaemonConfig,
        client: BackendClient,
        executors: Dict[str, BaseExecutor],
    ) -> None:
        self._config = config
        self._client = client
        self._executors = executors
        self._running = False
        self._current_job_id: str | None = None
        # model name → runtime name; rebuilt from each runtime's model
        # list (see _refresh_model_map).
        self._model_runtimes: Dict[str, str] = {}

        self._heartbeat = HeartbeatManager(
            client=client,
            worker_id=config.worker_id,
            interval=config.heartbeat_interval,
            get_loaded_models=self._get_loaded_models,
            get_loaded_model_digests=self._get_loaded_model_digests,
            get_inventory=self._get_inventory,
            declared_vram_gb=config.vram_gb,
        )

        # Ollama is the only runtime that can pull models on demand.
        self._ollama_executor = next(
            (ex for ex in executors.values() if isinstance(ex, OllamaExecutor)),
            None,
        )
        self._model_manager = None
        if self._ollama_executor is not None:
            self._model_manager = ModelManager(
                executor=self._ollama_executor,
                client=client,
                worker_id=config.worker_id,
            )

        # Ensure work directory exists
        self._work_dir = Path(config.work_dir)
        self._work_dir.mkdir(parents=True, exist_ok=True)

    async def _get_loaded_models(self) -> List[str]:
        """
        Models currently served by the runtimes, reported in heartbeats
        so the scheduler can prefer workers that already host a model.
        A single-runtime worker keeps its existing behavior (dynamic
        list, or the static config list when the runtime can't be
        queried); a mixed worker reports the union across runtimes.
        """
        if len(self._executors) == 1:
            executor = next(iter(self._executors.values()))
            if hasattr(executor, "list_models"):
                return await executor.list_models()
            return list(self._config.models)
        names: List[str] = []
        for executor in self._executors.values():
            if hasattr(executor, "list_models"):
                names.extend(await executor.list_models())
        return list(dict.fromkeys(names))

    async def _get_loaded_model_digests(self) -> dict:
        """name → digest map for loaded models (reproducibility pins).

        Union across runtimes. Empty for runtimes that can't report
        digests; the backend then falls back to name matching.
        """
        digests: dict = {}
        for executor in self._executors.values():
            if hasattr(executor, "list_models_detailed"):
                for m in await executor.list_models_detailed():
                    if m.get("name"):
                        digests[m["name"]] = m.get("digest")
        return digests

    async def _get_inventory(self) -> List[dict]:
        """Full on-disk inventory across all runtimes.

        Each executor tags its own items with its runtime name (so the
        backend's per-runtime rows come out right), and each item's
        `loaded` flag is scoped to the runtime that holds it — a model
        live on ollama is not "loaded" on the vllm row, even though the
        union loaded_models list contains it. BaseExecutor.inventory()
        never raises and returns [] where the runtime can't report.
        """
        items: List[dict] = []
        for executor in self._executors.values():
            loaded: set = set()
            if hasattr(executor, "list_models"):
                try:
                    loaded = set(await executor.list_models())
                except Exception:
                    loaded = set()
            for item in await executor.inventory():
                stamped = dict(item)
                if "loaded" not in stamped:
                    stamped["loaded"] = stamped.get("local_name") in loaded
                items.append(stamped)
        return items

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
        for name, executor in self._executors.items():
            try:
                await executor.close()
            except Exception as exc:
                # One executor that refuses to die must not keep the
                # others (or the client) from closing.
                logger.warning(f"Closing runtime '{name}' failed: {exc}")
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
        
        # ── Step 0: Route to the runtime that serves this model ─────
        # On a mixed worker a model belongs to exactly one runtime; on
        # a single-runtime worker this returns that executor no matter
        # what the job says.
        executor = await self._executor_for(job.model)
        if job.model and executor is None:
            # A model no runtime here hosts (stale dispatch, or a runtime
            # that stopped serving it). Never guess at a target — failing
            # the job lets the backend requeue it onto a worker that does
            # host the model.
            logger.error(
                f"[{job.job_id}] No runtime on this worker hosts model "
                f"'{job.model}' — failing the job"
            )
            await self._client.report_failure(
                job.job_id, f"No runtime on this worker hosts model '{job.model}'"
            )
            return

        # ── Step 0b: Ensure model is available (Ollama pulls) ─────────
        if self._model_manager and job.model and executor is self._ollama_executor:
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
        results = await self._run_prompts(prompts, job, executor)

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
        self, prompts: List[PromptRequest], job: Job,
        executor: Optional[BaseExecutor] = None,
    ) -> List[CompletionResult]:
        """
        Execute all prompts through a bounded pool of concurrent workers.

        Concurrency is fixed at ``config.max_concurrent_prompts`` for the life
        of the job. Deriving that number from the runtime instead of trusting
        the config value is issue #101.

        Every input row produces exactly one output row, in input order. No
        single prompt can fail the job: rejections are recorded per row before
        execution starts, and an unexpected exception inside a pool worker
        fails only the rows that worker was holding.

        `executor` is the runtime the job routed to; callers that don't
        resolve it themselves (tests, legacy paths) fall back to routing
        by job.model here.
        """
        if executor is None:
            executor = await self._executor_for(job.model)
        if executor is None:
            # No runtime hosts this model (mixed worker, unknown or
            # unserved name) — fail every row rather than guess at a
            # target.
            logger.error(
                f"[{job.job_id}] No runtime hosts model {job.model!r} "
                f"— failing all {len(prompts)} rows"
            )
            return [
                CompletionResult(
                    custom_id=p.custom_id,
                    error=f"NO_RUNTIME: no runtime on this worker hosts model {job.model!r}",
                )
                for p in prompts
            ]

        total = len(prompts)
        # Keyed by input index, not custom_id — duplicate ids still get their
        # own row, and out-of-order completion still writes in input order.
        results_by_index: dict[int, CompletionResult] = {}
        lock = asyncio.Lock()
        completed = 0
        failed = 0
        last_progress_report = 0.0

        # ── Partition ────────────────────────────────────────────
        # Every rejection is decided here, before any path forks, so one input
        # cannot get different treatment depending on the runtime it lands on.
        seen_ids: set[str] = set()
        chat_units: List[List[tuple[int, PromptRequest]]] = []
        embedding_rows: List[tuple[int, PromptRequest]] = []

        for idx, p in enumerate(prompts):
            if p.custom_id in seen_ids:
                # The backend's validator already rejects these at upload, so
                # this only fires on a bypass. Fail the row, not the job.
                logger.warning(
                    f"[{job.job_id}] Duplicate custom_id '{p.custom_id}' "
                    f"at row {idx} — failing that row"
                )
                results_by_index[idx] = CompletionResult(
                    custom_id=p.custom_id,
                    error=(
                        "DUPLICATE_CUSTOM_ID: custom_id "
                        f"'{p.custom_id}' appears more than once in the input"
                    ),
                )
                failed += 1
                continue
            seen_ids.add(p.custom_id)

            if p.body.get("stream"):
                # Applies to every endpoint — batch execution cannot honor
                # streaming and the response shape changes completely if
                # passed through.
                results_by_index[idx] = CompletionResult(
                    custom_id=p.custom_id,
                    error=(
                        "UNSUPPORTED_PARAMETER: stream=true is not "
                        "supported in batch mode. Batch requests are "
                        "executed synchronously and the streaming "
                        "response shape is incompatible."
                    ),
                )
                failed += 1
                continue

            if p.url == "/v1/embeddings":
                embedding_rows.append((idx, p))
            else:
                chat_units.append([(idx, p)])

        # Embeddings go through the same pool as chat. Where the runtime can
        # serve several rows in one request (Ollama's /api/embed), a chunk is
        # one unit of work; where it cannot, each row is its own unit and gets
        # the pool's concurrency instead of running serially after it.
        chunk_size = getattr(executor, "embedding_chunk_size", 1)
        if not isinstance(chunk_size, int) or chunk_size < 1:
            # Executors are free not to declare this, and test doubles often
            # don't. Anything unusable means "no coalescing".
            chunk_size = 1

        # Ask before chunking. A row the runtime cannot coalesce (Ollama
        # rejects list-valued inputs) would otherwise be swept into a chunk and
        # run one-at-a-time inside a single pool slot — so a job made entirely
        # of such rows collapsed to one worker no matter how the pool was
        # sized.
        can_coalesce = getattr(executor, "can_coalesce_embedding", None)
        if chunk_size > 1 and callable(can_coalesce):
            coalescable, solo = [], []
            for row in embedding_rows:
                try:
                    (coalescable if can_coalesce(row[1]) else solo).append(row)
                except Exception:
                    # A predicate that raises means "don't risk it".
                    solo.append(row)
        else:
            coalescable, solo = [], list(embedding_rows)

        embedding_units = [
            coalescable[i:i + chunk_size]
            for i in range(0, len(coalescable), chunk_size)
        ] + [[row] for row in solo]

        units = chat_units + embedding_units
        queue: asyncio.Queue = asyncio.Queue()
        for unit in units:
            queue.put_nowait(unit)

        async def _run_unit(
            unit: List[tuple[int, PromptRequest]]
        ) -> List[tuple[int, CompletionResult]]:
            """Execute one unit of work, mapping failures back to its rows."""
            unit_prompts = [p for _, p in unit]
            try:
                if len(unit) == 1 and unit_prompts[0].url != "/v1/embeddings":
                    results = [await executor.execute(unit_prompts[0])]
                else:
                    results = await executor.batch_execute(unit_prompts)
            except Exception as exc:
                # A crash here must cost only this unit's rows. Losing the
                # whole batch to one poisoned prompt is what the sequential
                # loop never did.
                logger.error(
                    f"[{job.job_id}] Unit of {len(unit)} prompt(s) raised: "
                    f"{exc!r}",
                    exc_info=True,
                )
                return [
                    (i, CompletionResult(
                        custom_id=p.custom_id,
                        error=f"INTERNAL_ERROR: {type(exc).__name__}: {exc}",
                    ))
                    for i, p in unit
                ]

            if len(unit) == 1:
                # One prompt in, one result out — it belongs to this row by
                # construction, so don't second-guess the custom_id.
                if results:
                    return [(unit[0][0], results[0])]
                return [(unit[0][0], CompletionResult(
                    custom_id=unit_prompts[0].custom_id,
                    error="INTERNAL_ERROR: executor returned no result for this prompt",
                ))]

            # Coalesced chunk: batch_execute gives no ordering guarantee, so
            # pair on custom_id rather than position.
            by_id = {r.custom_id: r for r in results}
            paired = []
            for i, p in unit:
                res = by_id.get(p.custom_id)
                if res is None:
                    res = CompletionResult(
                        custom_id=p.custom_id,
                        error="INTERNAL_ERROR: executor returned no result for this prompt",
                    )
                paired.append((i, res))
            return paired

        async def pool_worker() -> None:
            nonlocal completed, failed, last_progress_report

            while True:
                # Graceful shutdown check between units.
                if not self._running:
                    return

                try:
                    unit = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return

                logger.info(
                    f"[{job.job_id}] Executing "
                    f"{len(unit)} prompt(s) (id: {unit[0][1].custom_id}"
                    f"{'…' if len(unit) > 1 else ''})"
                )

                paired = await _run_unit(unit)

                should_report = False
                report_completed = 0
                report_failed = 0

                # Async-safe state updates — several workers land here at once.
                async with lock:
                    for i, result in paired:
                        results_by_index[i] = result
                        if result.is_success:
                            completed += 1
                            tokens = result.usage.get("total_tokens", "?")
                            logger.debug(
                                f"[{job.job_id}] Prompt {result.custom_id} "
                                f"completed ({tokens} tokens)"
                            )
                        else:
                            failed += 1
                            logger.warning(
                                f"[{job.job_id}] Prompt {result.custom_id} "
                                f"failed: {result.error}"
                            )

                    self._heartbeat.update_status(
                        status="busy",
                        job_id=job.job_id,
                        progress={
                            "total_prompts": total,
                            "completed_prompts": completed,
                            "failed_prompts": failed,
                        },
                    )

                    # Report progress to platform (time-throttled).
                    now = time.monotonic()
                    if (now - last_progress_report) >= self._config.progress_interval_seconds:
                        should_report = True
                        report_completed = completed
                        report_failed = failed
                        last_progress_report = now

                if should_report:
                    await self._client.report_progress(
                        job_id=job.job_id,
                        completed=report_completed,
                        failed=report_failed,
                        total=total,
                    )

        # ── Run the pool ─────────────────────────────────────────
        if units:
            pool_size = max(1, min(self._config.max_concurrent_prompts, len(units)))
            logger.info(
                f"[{job.job_id}] Running {len(units)} unit(s) "
                f"({len(chat_units)} chat, {len(embedding_rows)} embedding rows) "
                f"at concurrency {pool_size}"
            )
            workers = [
                asyncio.create_task(pool_worker()) for _ in range(pool_size)
            ]
            outcomes = await asyncio.gather(*workers, return_exceptions=True)
            for outcome in outcomes:
                if isinstance(outcome, BaseException):
                    # _run_unit already converts per-unit failures into rows, so
                    # reaching here means the pool loop itself broke. Log it and
                    # still return everything that finished.
                    logger.error(
                        f"[{job.job_id}] Pool worker crashed: {outcome!r}",
                        exc_info=outcome,
                    )

        if not self._running:
            logger.warning(
                f"[{job.job_id}] Shutdown requested — stopping early "
                f"({completed + failed}/{total} prompts done)"
            )

        # Final progress report — always sent, regardless of throttle.
        await self._client.report_progress(
            job_id=job.job_id,
            completed=completed,
            failed=failed,
            total=total,
        )

        # Return in input order, skipping anything shutdown left unrun.
        return [
            results_by_index[i]
            for i in range(total)
            if i in results_by_index
        ]

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

    # ── Model → Runtime Routing ──────────────────────────────────

    async def _executor_for(self, model: str) -> Optional[BaseExecutor]:
        """
        The executor that serves `model`, or None when no runtime on
        this worker does.

        A single-runtime worker routes everything to its one executor,
        unconditionally — the pre-multi-runtime behavior. A mixed
        worker consults the model→runtime map; a miss triggers one
        refresh, because models pulled on the box after registration
        are not in the stale map.
        """
        if len(self._executors) == 1:
            return next(iter(self._executors.values()))
        if not model:
            return None
        runtime = self._model_runtimes.get(model)
        if runtime and runtime in self._executors:
            return self._executors[runtime]
        await self._refresh_model_map()
        runtime = self._model_runtimes.get(model)
        if runtime:
            return self._executors.get(runtime)
        return None

    async def _refresh_model_map(self) -> None:
        """Rebuild the model→runtime map from each runtime's model list.

        First-writer-wins in config order: if two runtimes both claim a
        name, that is a misconfiguration — we log it and route to the
        first, rather than silently flip-flopping per job.
        """
        mapping: Dict[str, str] = {}
        for name, executor in self._executors.items():
            if not hasattr(executor, "list_models"):
                continue
            try:
                names = await executor.list_models()
            except Exception as exc:
                logger.debug(f"Could not list models from runtime '{name}': {exc}")
                continue
            for model in names:
                owner = mapping.get(model)
                if owner and owner != name:
                    logger.warning(
                        f"Model '{model}' is served by both '{owner}' and "
                        f"'{name}' — jobs for it will route to '{owner}'"
                    )
                    continue
                mapping[model] = name
        self._model_runtimes = mapping

    # ── Runtime Readiness ────────────────────────────────────────

    async def wait_for_runtimes(self) -> List[str]:
        """
        Wait until the configured runtimes report healthy, checking them
        concurrently (READINESS_MAX_RETRIES × READINESS_RETRY_DELAY each).

        Handles the case where the daemon starts before the runtimes are
        fully loaded. Returns the names of the runtimes that came up.
        Runtimes that don't are not fatal — main() decides what to
        advertise — but jobs for their models will fail per-row until
        they recover and a heartbeat revives their rows.
        """
        outcomes = await asyncio.gather(*(
            self._wait_one_runtime(name, executor)
            for name, executor in self._executors.items()
        ))
        ready = [name for name, ok in zip(self._executors, outcomes) if ok]
        for name, ok in zip(self._executors, outcomes):
            if ok:
                continue
            hint = ""
            if name == "vllm" and gpu_vendors_present() == ["amd"]:
                hint = f" {VLLM_ROCM_HINT}"
            logger.error(
                f"Runtime '{name}' health check failed after all retries — "
                f"its prompts may fail.{hint}"
            )
        if not ready:
            # The pre-multi-runtime behavior: a cold node where the
            # runtime(s) are still loading. Proceed anyway.
            logger.error("No runtime is healthy — proceeding anyway (prompts may fail).")
        else:
            logger.info(f"Ready runtimes: {', '.join(ready)}")

        # The routing map must exist before the first poll, or a job that
        # lands in the gap would have no route on a mixed worker.
        await self._refresh_model_map()
        return ready

    async def _wait_one_runtime(self, name: str, executor: BaseExecutor) -> bool:
        """True once `executor` reports healthy; False after the retries."""
        for attempt in range(1, READINESS_MAX_RETRIES + 1):
            try:
                healthy = await executor.health_check()
            except Exception as exc:
                logger.warning(f"Runtime '{name}' health check error: {exc}")
                healthy = False
            if healthy:
                logger.info(f"Runtime '{name}' health check passed")
                return True
            logger.warning(
                f"Runtime '{name}' not ready — retry {attempt}/{READINESS_MAX_RETRIES} "
                f"in {READINESS_RETRY_DELAY:.0f}s..."
            )
            await asyncio.sleep(READINESS_RETRY_DELAY)
        return False

    # ── Signal Handling ──────────────────────────────────────────

    def _install_signal_handlers(self) -> None:
        """Register OS signal handlers for graceful shutdown."""
        loop = asyncio.get_running_loop()

        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self._handle_shutdown_signal, sig)
            except NotImplementedError:
                # Not supported on Windows event loops. Signal-driven graceful
                # shutdown is unavailable there, but Ctrl+C still raises
                # KeyboardInterrupt.
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
