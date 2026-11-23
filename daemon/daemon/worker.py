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
import inspect
import random
import shutil
import signal
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import httpx

from daemon.client import BackendClient
from daemon.config import DaemonConfig
from daemon.executors.base import BaseExecutor
from daemon.executor_factory import VLLM_ROCM_HINT
from daemon.hardware import gpu_vendors_present
from daemon.journal import JOURNAL_FILENAME, ResultJournal, is_final
from daemon.journal import load as load_journal
from daemon.log import get_logger
from daemon.models import CompletionResult, Job, PromptRequest
from daemon.heartbeat import HeartbeatManager
from daemon.model_manager import ModelManager

# Present in a job's directory while this daemon is running that job.
IN_FLIGHT_MARKER = "in_flight"
# Written into the marker when a graceful stop could not hand the batch back,
# so the restart's hand-back is still a graceful one (no attempt spent).
SHUTDOWN_MARKER_TEXT = "shutdown"


class JobInterrupted(Exception):
    """A stop signal ended the job before every row ran.

    Raised by the prompt runner instead of uploading a partial result as final.
    The journal is closed by then; the caller hands the batch back.
    """

# A job directory with no in-flight marker, untouched this long, is swept: its
# batch has finished (here or on another worker) or expired. Matches the
# default 24h completion window, so a journal older than that has nothing left
# to resume.
STALE_JOB_DIR_SECONDS = 24 * 3600
STALE_JOB_SWEEP_SECONDS = 3600

logger = get_logger(__name__)

#: Startup readiness wait, per runtime: 12 × 5s = 60s max. Runtimes are
#: checked concurrently, so a mixed node waits 60s total, not 60s each.
READINESS_MAX_RETRIES = 12
READINESS_RETRY_DELAY = 5.0

# A per-beat health check must not hold up the beat. Executors bound their
# own requests, but a runtime that accepts a connection and then stalls
# would otherwise stretch the heartbeat interval.
LIVENESS_PROBE_TIMEOUT = 8.0


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
        # Set by a stop signal. _running alone stops new units from starting;
        # this also starts the grace period for units already running.
        self._stop_requested = asyncio.Event()
        self._current_job_id: str | None = None
        # model name → runtime name; rebuilt from each runtime's model
        # list (see _refresh_model_map).
        self._model_runtimes: Dict[str, str] = {}
        # runtime name → reachable, as of the last beat. Seeded by
        # wait_for_runtimes so the first beat reports startup's own finding
        # rather than a transition away from nothing.
        self._runtime_live: Dict[str, bool] = {}

        self._heartbeat = HeartbeatManager(
            client=client,
            worker_id=config.worker_id,
            interval=config.heartbeat_interval,
            get_loaded_models=self._get_loaded_models,
            get_loaded_model_digests=self._get_loaded_model_digests,
            get_inventory=self._get_inventory,
            get_runtime_statuses=self._get_runtime_statuses,
            declared_vram_gb=config.vram_gb,
            runtimes=list(executors),
        )

        # One manager per runtime that declares a fetch path. A runtime
        # absent from this map obtains nothing on demand, so a job naming a
        # model it does not hold carries that runtime's own error.
        self._model_managers: Dict[str, ModelManager] = {
            runtime: ModelManager(executor=ex, client=client, worker_id=config.worker_id)
            for runtime, ex in executors.items()
            if ex.supports_pull
        }

        # Ensure work directory exists
        self._work_dir = Path(config.work_dir)
        self._work_dir.mkdir(parents=True, exist_ok=True)
        # Job ids a previous process died running; found on the first poll.
        self._interrupted_jobs: Optional[List[str]] = None
        self._last_job_dir_sweep = 0.0

    async def _get_loaded_models(self) -> List[str]:
        """Models resident in VRAM, union across runtimes, for the heartbeat.

        The scheduler prefers a worker that already holds the model, so this
        is residence and not availability: the configured model list and the
        on-disk catalogue both name models this worker merely *could* serve,
        and reporting either makes the preference meaningless.
        """
        names: List[str] = []
        for executor in self._executors.values():
            names.extend(await executor.list_running_models())
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

        `loaded` is residence, so it is stamped from list_running_models()
        and not from the inventory itself: testing an on-disk listing for
        membership of an on-disk listing is true by construction.

        The residence query runs once per runtime and only when an item
        actually needs stamping, so a runtime that reports no inventory —
        the shape of one that is down — costs one failed call per beat
        instead of two.
        """
        items: List[dict] = []
        for executor in self._executors.values():
            resident: Optional[set] = None
            footprints: Optional[Dict[str, dict]] = None
            for item in await executor.inventory():
                stamped = dict(item)
                if "loaded" not in stamped:
                    if resident is None:
                        try:
                            resident = set(await executor.list_running_models())
                        except Exception:
                            resident = set()
                    stamped["loaded"] = stamped.get("local_name") in resident
                if stamped["loaded"]:
                    # What the runtime says the loaded model occupies, so the
                    # backend can calibrate this runtime's KV multiplier (#164).
                    if footprints is None:
                        footprints = await executor.running_footprints()
                    seen = footprints.get(stamped.get("local_name")) or {}
                    stamped["size_vram_bytes"] = seen.get("size_vram")
                    stamped["loaded_context"] = seen.get("context_length")
                items.append(stamped)
        return items

    async def _get_runtime_statuses(self) -> List[dict]:
        """Per-runtime liveness for the heartbeat, probed fresh each beat.

        Startup readiness answers "can this worker serve anything yet"; this
        answers "which runtimes can it serve *now*", which is a different
        question once a provider stops one server and starts another. Without
        it the control plane keeps offering models from a runtime that died
        and never learns about one that arrived, until the daemon restarts.

        A runtime that comes back also needs its models routable again, so a
        change in either direction rebuilds the model map. The map is what
        `_executor_for` consults, and it is otherwise built once at startup.

        Never raises: a probe that errors or hangs reports `unavailable`,
        which is what the runtime being unreachable means.
        """
        async def probe(name: str, executor: BaseExecutor) -> bool:
            try:
                return bool(await asyncio.wait_for(
                    executor.health_check(), timeout=LIVENESS_PROBE_TIMEOUT,
                ))
            except Exception as exc:
                logger.debug(f"Runtime '{name}' liveness probe failed: {exc}")
                return False

        names = list(self._executors)
        outcomes = await asyncio.gather(*(
            probe(name, self._executors[name]) for name in names
        ))
        live = {name: ok for name, ok in zip(names, outcomes)}

        for name, ok in live.items():
            was = self._runtime_live.get(name)
            if was is ok:
                continue
            logger.info(
                "Runtime '%s' is now %s", name, "reachable" if ok else "unreachable",
            )
        if live != self._runtime_live:
            self._runtime_live = live
            await self._refresh_model_map()
            # Read the map rather than re-listing: it was just built from
            # these same calls, and it is also the truth about routing —
            # including a carried-over entry for a runtime whose listing
            # failed, which a fresh call would not show.
            served: Dict[str, List[str]] = {}
            for model, owner in self._model_runtimes.items():
                served.setdefault(owner, []).append(model)
            for name, ok in live.items():
                if not ok:
                    continue
                names = served.get(name, [])
                logger.info(
                    "Runtime '%s' serves: %s",
                    name, ", ".join(names) if names else "(nothing)",
                )

        return [
            {"type": name, "status": "ready" if ok else "unavailable"}
            for name, ok in live.items()
        ]

    # ── Public API ───────────────────────────────────────────────

    def update_worker_id(self, worker_id: str) -> None:
        """Adopt the backend-assigned worker id after registration.

        Registration happens after the Worker is constructed, so the
        heartbeat and model-manager ids snapshotted in __init__ are
        local placeholders until this runs. Call it alongside
        client.update_worker_id().
        """
        self._heartbeat.update_worker_id(worker_id)
        for mm in self._model_managers.values():
            mm.update_worker_id(worker_id)

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
        self.request_stop()
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
        await self._reclaim_interrupted_jobs()

        logger.debug("Polling for jobs...")
        job = await self._client.poll_job()

        if job is None:
            logger.debug("No jobs available — will retry")
            return

        self._current_job_id = job.job_id
        logger.info(f"{'='*60}")
        logger.info(f"JOB STARTED: {job.job_id}")
        logger.info(f"{'='*60}")

        marker = self._work_dir / job.job_id / IN_FLIGHT_MARKER
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.touch()
        except OSError as exc:
            logger.warning(f"[{job.job_id}] Could not write in-flight marker: {exc}")

        settled = False
        try:
            settled = await self._execute_job(job)
        except Exception as exc:
            logger.error(
                f"Job {job.job_id} failed with error: {exc}",
                exc_info=True,
            )
            # Report failure to backend so it can requeue the job
            # (spec §11: worker reports failure)
            settled = await self._client.report_failure(job.job_id, str(exc))
        finally:
            self._current_job_id = None

        if settled:
            # The backend acknowledged an outcome (an upload, or a definitive
            # answer to a failure report), so the batch is not stranded here.
            self._clear_marker(job.job_id)
        else:
            # Seen through but never acknowledged: the upload and the failure
            # report both failed. The batch is still assigned to this worker
            # and heartbeats keep the sweeper off it. Keep the marker, so a
            # restart finds it, and hand the batch back before the next poll.
            logger.warning(
                f"[{job.job_id}] Backend never acknowledged this job's outcome; "
                f"will hand the batch back before the next poll"
            )
            if self._interrupted_jobs is not None and job.job_id not in self._interrupted_jobs:
                self._interrupted_jobs.append(job.job_id)

    def _clear_marker(self, job_id: str) -> None:
        try:
            (self._work_dir / job_id / IN_FLIGHT_MARKER).unlink(missing_ok=True)
        except OSError as exc:
            # If it is left behind, a restart hands the batch back again. The
            # backend answers 403/404/409, and that answer settles it.
            logger.warning(f"[{job_id}] Could not remove in-flight marker: {exc}")

    async def _reclaim_interrupted_jobs(self) -> None:
        """Hand back batches a previous run of this daemon died holding.

        A daemon restarted after a crash keeps its worker_id and heartbeats
        again within seconds, so the backend never sees it go stale and never
        requeues the batch it was running — and /workers/poll only hands out
        unassigned work. Without this the batch sits in_progress forever and
        its journal is never read.

        The same queue also carries jobs this process saw through but could
        not get acknowledged, because the upload and the failure report both
        failed.

        Reporting the failure requeues the batch. The report carries the
        journalled count, and the backend spends one of MAX_BATCH_ATTEMPTS
        only when that count has not grown since the last hand-back. A daemon
        in a crash loop that is still making progress does not burn the batch.
        If the batch comes back to this worker, the journal resumes it. Runs
        before each poll until every leftover marker has been delivered, so a
        backend that is down at startup is retried rather than skipped.

        Also sweeps abandoned job directories (``_sweep_stale_job_dirs``).
        """
        if self._interrupted_jobs is None:
            self._interrupted_jobs = sorted(
                m.parent.name for m in self._work_dir.glob(f"*/{IN_FLIGHT_MARKER}")
            )
        self._sweep_stale_job_dirs()
        while self._interrupted_jobs:
            job_id = self._interrupted_jobs[0]
            try:
                journalled = len(load_journal(self._work_dir / job_id / JOURNAL_FILENAME))
            except OSError as exc:
                # The count is only a log line and a progress hint. This loop
                # runs before every poll, so raising here would stop the
                # worker taking any work at all.
                logger.warning(f"[{job_id}] Could not read journal during recovery: {exc}")
                journalled = 0
            try:
                marker_text = (self._work_dir / job_id / IN_FLIGHT_MARKER).read_text(
                    encoding="utf-8"
                ).strip()
            except OSError:
                marker_text = ""
            # A graceful stop that could not reach the backend at the time:
            # still graceful, so still free.
            graceful = marker_text == SHUTDOWN_MARKER_TEXT
            logger.warning(
                f"[{job_id}] Job {'stopped gracefully' if graceful else 'interrupted'} "
                f"or never acknowledged ({journalled} prompts journalled); "
                f"handing the batch back"
            )
            delivered = await self._client.report_failure(
                job_id,
                f"{'worker_shutdown: ' if graceful else ''}worker handed the batch "
                f"back mid-job; {journalled} completed prompts journalled on this worker",
                journalled=journalled,
                shutdown=graceful,
            )
            if not delivered:
                return  # not acknowledged; try again before the next poll
            self._clear_marker(job_id)
            self._interrupted_jobs.pop(0)

    def _sweep_stale_job_dirs(self) -> None:
        """Delete job directories nothing will read again.

        A journal has to outlive its hand-back, because the batch may come
        back here to resume. So a directory cannot go the moment its marker
        does. Instead, a directory with no marker that has been untouched for
        STALE_JOB_DIR_SECONDS is removed: by then its batch has finished or
        expired. That also covers a requeued batch that another worker
        finished, which this worker never hears about. Runs at most once per
        STALE_JOB_SWEEP_SECONDS.
        """
        now = time.time()
        if now - self._last_job_dir_sweep < STALE_JOB_SWEEP_SECONDS:
            return
        self._last_job_dir_sweep = now
        try:
            job_dirs = [d for d in self._work_dir.iterdir() if d.is_dir()]
        except OSError as exc:
            logger.warning(f"Could not list {self._work_dir} for cleanup: {exc}")
            return
        for job_dir in job_dirs:
            if job_dir.name == self._current_job_id:
                continue
            try:
                if (job_dir / IN_FLIGHT_MARKER).exists():
                    continue
                newest = max(
                    [job_dir.stat().st_mtime]
                    + [f.stat().st_mtime for f in job_dir.iterdir()]
                )
                if now - newest < STALE_JOB_DIR_SECONDS:
                    continue
                shutil.rmtree(job_dir)
                logger.info(f"Removed stale job directory {job_dir}")
            except OSError as exc:
                logger.warning(f"Could not remove stale job directory {job_dir}: {exc}")

    async def _execute_job(self, job: Job) -> bool:
        """
        Full lifecycle of a single job:
            download → parse → execute → write → upload

        Returns whether the backend acknowledged the outcome: the upload went
        through, or a failure report got a definitive answer. False means the
        batch may still be assigned here, so the caller keeps it for hand-back.
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
            # A model no runtime's list currently reports. Ollama can
            # pull it on demand (a single-runtime Ollama worker always
            # takes the Step 0b path below) — try the pull before
            # failing, then re-resolve.
            executor = await self._pull_unrouted_model(job)
        if job.model and executor is None:
            # A model no runtime here hosts (stale dispatch, or a
            # runtime that stopped serving it — and a failed pull).
            # Never guess at a target — failing the job lets the
            # backend requeue it onto a worker that does host the model.
            logger.error(
                f"[{job.job_id}] No runtime on this worker hosts model "
                f"'{job.model}' — failing the job"
            )
            return await self._client.report_failure(
                job.job_id, f"No runtime on this worker hosts model '{job.model}'"
            )

        # ── Step 0b: Ensure model is available (pull if needed) ────────
        # The executor identifies its runtime, and the runtime selects the
        # manager. A runtime with no fetch path has no manager here, so the
        # job proceeds to execution and fails on the runtime's own error.
        if job.model and executor is not None:
            runtime_name = next(
                (r for r, ex in self._executors.items() if ex is executor),
                None,
            )
            mm = self._model_managers.get(runtime_name) if runtime_name else None
            if mm:
                self._heartbeat.update_status("downloading_model", job.job_id)
                available = await mm.ensure_model(job.model)
                if not available:
                    self._heartbeat.update_status("idle")
                    return await self._client.report_failure(
                        job.job_id, f"Failed to download model: {job.model}"
                    )

        # A stop that arrived while the model was being fetched: there is
        # nothing to run yet, so hand the batch straight back.
        if not self._running:
            return await self._hand_back_on_shutdown(job)

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
            return await self._client.report_failure(
                job.job_id, "Input file is empty — no prompts to process"
            )

        # ── Step 3: Execute each prompt ──────────────────────────
        # Each result is journalled as it completes; a rerun of this job on
        # this worker (after a crash or requeue) resumes from the journal.
        try:
            results = await self._run_prompts(
                prompts, job, executor, journal_path=job_dir / JOURNAL_FILENAME,
            )
        except JobInterrupted:
            return await self._hand_back_on_shutdown(job)

        # ── Step 4: Write output JSONL ───────────────────────────
        self._write_output(output_path, results)

        # ── Step 5: Upload results (with real counts) ────────────
        successes = sum(1 for r in results if r.is_success)
        failures = total - successes

        logger.info(f"[{job.job_id}] Uploading results...")
        await self._client.upload_results(
            job.job_id, output_path, completed=successes, failed=failures
        )
        # The backend has the results; a journal kept past this point is only
        # a second copy of output.jsonl, and a stale one to resume from.
        try:
            (job_dir / JOURNAL_FILENAME).unlink(missing_ok=True)
        except OSError as exc:
            logger.warning(f"[{job.job_id}] Could not delete journal after upload: {exc}")

        # ── Summary ──────────────────────────────────────────────
        total_tokens = sum(
            r.usage.get("total_tokens", 0) for r in results
        )

        if not self._running:
            logger.warning(
                f"[{job.job_id}] Job stopped by shutdown! "
                f"Results: {successes}/{total} succeeded, "
                f"{failures} failed, {total_tokens:,} total tokens"
            )
        else:
            logger.info(f"[{job.job_id}] Job completed!")
            logger.info(
                f"[{job.job_id}] Results: {successes}/{total} succeeded, "
                f"{failures} failed, {total_tokens:,} total tokens"
            )
        
        self._heartbeat.update_status("idle")
        return True

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
                        prompt.num_ctx = job.num_ctx

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
        journal_path: Optional[Path] = None,
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

        With `journal_path`, every result the executor produces is appended
        there as it lands, and rows an earlier run already journalled are
        restored instead of executed (see daemon/journal.py). Restored rows
        count toward progress, so a resumed job does not appear to restart
        near zero. Skipping on custom_id is safe because the backend rejects
        duplicate custom_ids at upload (batch_validator.check_custom_id).
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

        # The journal is closed (and fsynced) however the run ends.
        journal = ResultJournal.open(journal_path) if journal_path is not None else None
        try:
            return await self._run_pool(prompts, job, executor, journal)
        finally:
            if journal is not None:
                journal.close()

    async def _run_pool(
        self, prompts: List[PromptRequest], job: Job,
        executor: BaseExecutor, journal: Optional[ResultJournal],
    ) -> List[CompletionResult]:
        """The body of _run_prompts once the runtime and journal are settled."""
        total = len(prompts)
        # Keyed by input index, not custom_id — duplicate ids still get their
        # own row, and out-of-order completion still writes in input order.
        results_by_index: dict[int, CompletionResult] = {}
        lock = asyncio.Lock()
        completed = 0
        failed = 0
        last_progress_report = 0.0

        restored: Dict[str, CompletionResult] = journal.restored if journal else {}
        resumed_ok = 0
        resumed_failed = 0
        resumed_tokens = 0

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

            # Already executed by an earlier run of this job. Errors included:
            # a failed row was still executed, and re-running it would retry a
            # deterministic failure forever. Placed after the duplicate check
            # so a duplicate row never inherits its twin's journalled result.
            prior = restored.get(p.custom_id)
            if prior is not None:
                results_by_index[idx] = prior
                if prior.is_success:
                    completed += 1
                    resumed_ok += 1
                    resumed_tokens += int(prior.usage.get("total_tokens", 0) or 0)
                else:
                    failed += 1
                    resumed_failed += 1
                continue

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

        if resumed_ok or resumed_failed:
            logger.info(
                f"[{job.job_id}] Resuming from journal: "
                f"{resumed_ok + resumed_failed}/{total} prompts already done "
                f"({resumed_ok} succeeded, {resumed_failed} failed, "
                f"{resumed_tokens:,} tokens) — skipping them, "
                f"{sum(len(u) for u in units)} left to run"
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

                if journal is not None:
                    # Keyed on the input row's id, never the executor's echo of
                    # it, so a restored row lands where it ran. Transient
                    # failures stay out so a resume retries them.
                    #
                    # Appended before the lock is taken: the append is
                    # synchronous, so no other pool worker can interleave with
                    # it, and acquiring the lock is a cancellation point — a
                    # stop landing there would otherwise discard a row whose
                    # inference had already finished.
                    journal.append(
                        r if r.custom_id == prompts[i].custom_id
                        else r.model_copy(update={"custom_id": prompts[i].custom_id})
                        for i, r in paired
                        if is_final(r)
                    )

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

        if chat_units:
            await self._prepare_runtime(executor, job)

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
            outcomes = await self._await_pool(workers, job)
            for outcome in outcomes:
                if isinstance(outcome, asyncio.CancelledError):
                    continue  # cut off at the end of the shutdown grace period
                if isinstance(outcome, BaseException):
                    # _run_unit already converts per-unit failures into rows, so
                    # reaching here means the pool loop itself broke. Log it and
                    # still return everything that finished.
                    logger.error(
                        f"[{job.job_id}] Pool worker crashed: {outcome!r}",
                        exc_info=outcome,
                    )

        unrun = total - len(results_by_index)
        if unrun and not self._running:
            # A graceful stop. Uploading now would complete the batch with
            # every unrun row counted as a failure. Instead the job ends here:
            # what ran is in the journal (closed by _run_prompts), nothing is
            # reported failed, and the caller hands the batch back to resume.
            logger.warning(
                f"[{job.job_id}] Shutdown requested — stopping with "
                f"{total - unrun}/{total} prompts done, {unrun} left to resume"
            )
            raise JobInterrupted(job.job_id)

        # Rows stranded by a crashed pool worker (not a shutdown) have no
        # result at all. Synthesize an explicit failure per row index so the
        # output file has exactly one line per input prompt, duplicate
        # custom_ids stay isolated, and the backend gets honest counts.
        unrun_error = "INTERNAL_ERROR: prompt not executed — pool worker stopped early"
        for i, prompt in enumerate(prompts):
            if i not in results_by_index:
                logger.warning(
                    f"[{job.job_id}] Prompt {prompt.custom_id} (row {i}) was not "
                    f"executed — recording as failure: {unrun_error}"
                )
                results_by_index[i] = CompletionResult(
                    custom_id=prompt.custom_id,
                    error=unrun_error,
                )
                failed += 1

        # Final progress report — always sent, regardless of throttle.
        await self._client.report_progress(
            job_id=job.job_id,
            completed=completed,
            failed=failed,
            total=total,
        )

        # Return in input order (now guaranteed to have an entry for every row).
        return [results_by_index[i] for i in range(total)]

    async def _prepare_runtime(self, executor: BaseExecutor, job: Job) -> None:
        """Let the runtime load the job's model before the pool fans out.

        A load can take minutes, so a stop signal ends the wait at once; the
        pool then starts nothing and the batch is handed back. Any failure
        is logged and the pool runs anyway, loading on its first prompt.
        """
        prepare = getattr(executor, "prepare", None)
        if not callable(prepare) or not self._running:
            return
        try:
            pending = prepare(job.model, job.num_ctx)
        except Exception as exc:
            logger.warning(f"[{job.job_id}] Runtime prepare raised {exc!r}; prompts will load the model")
            return
        if not inspect.isawaitable(pending):
            return
        task = asyncio.ensure_future(pending)
        stop = asyncio.ensure_future(self._stop_requested.wait())
        try:
            await asyncio.wait({task, stop}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            stop.cancel()
            if not task.done():
                task.cancel()
                logger.info(f"[{job.job_id}] Stop requested — model load abandoned")
            elif not task.cancelled() and task.exception() is not None:
                logger.warning(
                    f"[{job.job_id}] Runtime prepare raised {task.exception()!r}; "
                    f"prompts will load the model"
                )

    async def _await_pool(self, workers: List[asyncio.Task], job: Job) -> list:
        """Wait for the pool, bounding how long a stop signal waits for it.

        Pool workers stop taking units once _running is False, but a unit
        already running can take up to inference_timeout (300s by default),
        past systemd's SIGKILL. After a stop, units in flight get
        shutdown_grace_seconds, then they are cancelled. A cancelled unit was
        never journalled, so the resume runs it again.
        """
        gathered = asyncio.gather(*workers, return_exceptions=True)
        stop = asyncio.ensure_future(self._stop_requested.wait())
        try:
            done, _ = await asyncio.wait(
                {gathered, stop}, return_when=asyncio.FIRST_COMPLETED,
            )
            if gathered not in done:
                grace = self._config.shutdown_grace_seconds
                logger.warning(
                    f"[{job.job_id}] Stop requested — waiting up to {grace:g}s "
                    f"for prompts already running"
                )
                done, _ = await asyncio.wait({gathered}, timeout=grace)
                if gathered not in done:
                    logger.warning(
                        f"[{job.job_id}] Grace period over — cancelling prompts "
                        f"still running; the resume will run them again"
                    )
                    for w in workers:
                        w.cancel()
            return await gathered
        except asyncio.CancelledError:
            for w in workers:
                w.cancel()
            raise
        finally:
            stop.cancel()

    async def _hand_back_on_shutdown(self, job: Job) -> bool:
        """Give the batch back after a graceful stop, without spending an attempt.

        Nothing is uploaded and nothing reported as failed: the batch is
        requeued with shutdown=True, which the backend does not count against
        MAX_BATCH_ATTEMPTS. A provider stepping away politely can never fail a
        batch. The journal stays so the batch resumes if it comes back here.

        Returns whether the backend acknowledged. If it did not (backend down
        during the stop), the marker is left saying so, and the next start's
        hand-back is still a graceful one.
        """
        job_dir = self._work_dir / job.job_id
        try:
            journalled = len(load_journal(job_dir / JOURNAL_FILENAME))
        except OSError as exc:
            logger.warning(f"[{job.job_id}] Could not read journal for hand-back: {exc}")
            journalled = 0
        try:
            (job_dir / IN_FLIGHT_MARKER).write_text(SHUTDOWN_MARKER_TEXT, encoding="utf-8")
        except OSError as exc:
            logger.warning(f"[{job.job_id}] Could not record shutdown in marker: {exc}")

        delivered = await self._client.report_failure(
            job.job_id,
            f"worker_shutdown: provider stopped the daemon mid-batch; "
            f"{journalled} completed prompts journalled on this worker",
            journalled=journalled,
            shutdown=True,
        )
        if delivered:
            logger.info(
                f"[{job.job_id}] Batch handed back for resume "
                f"({journalled} prompts journalled)"
            )
        self._heartbeat.update_status("idle")
        return delivered

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

        A runtime whose list_models() raised keeps its previous entries:
        a failed query is not an empty model set, and dropping them would
        leave its models unroutable for the whole outage window. The
        carry-over is applied in a second pass after the loop, so a live
        claim always wins: a stale entry must not keep routing a model to
        a runtime that is down when a healthy runtime now serves it.
        """
        mapping: Dict[str, str] = {}
        carried: List[Tuple[str, str]] = []
        for name, executor in self._executors.items():
            if not hasattr(executor, "list_models"):
                continue
            try:
                names = await executor.list_models()
            except Exception as exc:
                logger.debug(f"Could not list models from runtime '{name}': {exc}")
                carried.extend(
                    (model, name)
                    for model, owner in self._model_runtimes.items()
                    if owner == name
                )
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
        for model, name in carried:
            mapping.setdefault(model, name)
        self._model_runtimes = mapping

    async def _pull_unrouted_model(self, job: Job) -> Optional[BaseExecutor]:
        """Route an unrouted model by pulling it via a runtime's fetch path.

        A mixed worker's map only knows what is already on the box, so a
        catalogue model dispatched here before its first pull would fail
        outright. Every runtime declaring a fetch path is tried in turn and
        the first success wins. None when no runtime can fetch, the fetch
        fails, or the model is still unroutable afterwards.
        """
        if not self._model_managers:
            return None
        self._heartbeat.update_status("downloading_model", job.job_id)
        for runtime, mm in self._model_managers.items():
            available = await mm.ensure_model(job.model)
            if available:
                executor = await self._executor_for(job.model)
                if executor is not None:
                    return executor
        self._heartbeat.update_status("idle")
        return None

    # ── Runtime Readiness ────────────────────────────────────────

    async def wait_for_runtimes(self) -> List[str]:
        """
        Wait until the configured runtimes report healthy, checking them
        concurrently (READINESS_MAX_RETRIES × READINESS_RETRY_DELAY each).

        Handles the case where the daemon starts before the runtimes are
        fully loaded. Returns the names of the runtimes that came up.
        Runtimes that don't are not fatal — they are registered anyway,
        and heartbeats fill their rows in once they recover — but jobs
        for their models will fail per-row until they do.
        """
        outcomes = await asyncio.gather(*(
            self._wait_one_runtime(name, executor)
            for name, executor in self._executors.items()
        ))
        ready = [name for name, ok in zip(self._executors, outcomes) if ok]
        self._runtime_live = dict(zip(self._executors, outcomes))
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

        # Name the models each runtime actually answers to. Dispatch matches
        # on these, so a name that is not in the catalogue means this worker
        # is online, healthy and unreachable — llama.cpp's `--alias` being
        # the easiest way to get there. Printing them is what lets a provider
        # compare against the catalogue without reading the database.
        for engine in ready:
            names = await self._executors[engine].list_models()
            logger.info(
                "Runtime '%s' serves: %s",
                engine, ", ".join(names) if names else "(nothing)",
            )
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
                f"Currently processing job {self._current_job_id} — prompts "
                f"already running get up to {self._config.shutdown_grace_seconds:g}s "
                f"to finish, then the batch is handed back to resume later"
            )

        self.request_stop()

    def request_stop(self) -> None:
        """Stop taking work; start the grace period for prompts in flight."""
        self._running = False
        self._stop_requested.set()
