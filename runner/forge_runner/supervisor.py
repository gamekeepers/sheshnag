"""Run one job end to end: data in, engine supervised, adapter or failure out.

    download dataset(s) -> write config -> start engine
      -> every few seconds: forward new trainer_log.jsonl lines as progress,
         heartbeat, stop the engine if the backend says the job was cancelled
      -> exit 0: zip the adapter and upload it
      -> otherwise: classify the engine's output and report a failure

Training is never restarted from here. A failure is reported once, with
`retryable` saying whether running it again could help; the backend decides.
"""
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import httpx

from forge_runner import artifact, config
from forge_runner.errors import classify
from forge_runner.job import Job
from forge_runner.progress import TrainerLogTail, to_progress

logger = logging.getLogger(__name__)


@dataclass
class RunResult:
    status: str               # succeeded | failed | cancelled
    detail: str = ""


def run_job(job: Job, client, engine, work_root: Path, worker_id: str, *,
            poll_interval: float = 2.0,
            heartbeat: Optional[Callable[[], None]] = None) -> RunResult:
    workdir = (work_root / job.job_id).resolve()
    (workdir / "data").mkdir(parents=True, exist_ok=True)

    try:
        client.download(job.dataset_path, workdir / "data" / "train.jsonl")
        has_validation = bool(job.validation_file_id)
        if has_validation:
            client.download(f"/v1/files/{job.validation_file_id}/content",
                            workdir / "data" / "validation.jsonl")
        config_path = config.write(job, workdir, has_validation=has_validation)
    except config.UnsupportedJob as exc:
        client.report_failure(job.job_id, worker_id, error=str(exc),
                              code="unsupported_job", retryable=False)
        return RunResult("failed", str(exc))
    except Exception as exc:  # download failed: network, auth, missing file
        message = f"forge-runner could not fetch the job's data: {exc}"
        client.report_failure(job.job_id, worker_id, error=message,
                              code="dataset_download_failed", retryable=True)
        return RunResult("failed", message)

    log_path = workdir / "engine.log"
    try:
        proc = engine.start(config_path, workdir, f"forge-{job.job_id}", log_path)
    except Exception as exc:  # e.g. FileNotFoundError: docker not on PATH
        # Nothing reaches engine.log in this case, so errors.classify's
        # engine_not_started rule can't fire; report it directly.
        message = f"The training engine could not be started on this worker: {exc}"
        logger.error("Job %s: %s", job.job_id, message)
        client.report_failure(job.job_id, worker_id, error=message,
                              code="engine_not_started", retryable=True)
        return RunResult("failed", message)
    tail = TrainerLogTail(workdir / "out" / "trainer_log.jsonl")
    logger.info("Job %s: engine started, log at %s", job.job_id, log_path)

    try:
        while True:
            rc = proc.poll()
            for entry in tail.read_new():
                fields = to_progress(entry)
                if fields and client.progress(job.job_id, worker_id, **fields):
                    logger.info("Job %s cancelled by the backend; stopping the engine", job.job_id)
                    proc.stop()
                    return RunResult("cancelled")
            if heartbeat:
                heartbeat()
            if rc is not None:
                break
            time.sleep(poll_interval)
    except BaseException:
        # Whatever escapes (including Ctrl-C), never leave a container
        # training on a shared GPU with nobody supervising it.
        proc.stop()
        raise

    if rc != 0:
        failure = classify(log_path.read_text(encoding="utf-8", errors="replace"), rc)
        logger.warning("Job %s failed (%s): %s", job.job_id, failure.code, failure.message)
        client.report_failure(job.job_id, worker_id, error=failure.message,
                              code=failure.code, retryable=failure.retryable)
        return RunResult("failed", failure.message)

    out_dir = workdir / "out"
    try:
        zip_path = artifact.package(out_dir, workdir / f"{job.job_id}_adapter.zip")
    except artifact.MissingAdapter as exc:
        client.report_failure(job.job_id, worker_id, error=str(exc),
                              code="adapter_missing", retryable=True)
        return RunResult("failed", str(exc))

    try:
        client.upload_adapter(job.job_id, worker_id, zip_path,
                              engine=engine.name, engine_version=engine.version,
                              engine_image=engine.image, metrics=artifact.read_metrics(out_dir))
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 409:
            # Cancelled between the last progress report and the upload.
            logger.info("Job %s was cancelled before its adapter was uploaded; kept at %s",
                        job.job_id, zip_path)
            return RunResult("cancelled", str(zip_path))
        return _upload_failed(client, job, worker_id, zip_path, exc)
    except httpx.HTTPError as exc:  # network: timeout, connection reset
        return _upload_failed(client, job, worker_id, zip_path, exc)
    logger.info("Job %s: adapter uploaded", job.job_id)
    return RunResult("succeeded", str(zip_path))


def _upload_failed(client, job: Job, worker_id: str, zip_path: Path, exc: Exception) -> RunResult:
    message = f"Training finished but the adapter upload failed: {exc}"
    logger.error("Job %s: %s (adapter kept at %s)", job.job_id, message, zip_path)
    client.report_failure(job.job_id, worker_id, error=message,
                          code="adapter_upload_failed", retryable=True)
    return RunResult("failed", message)
