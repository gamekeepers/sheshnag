"""forge-runner CLI.

    forge-runner serve      poll Sheshnag for finetune jobs and run them
    forge-runner run-local  run one job from a local JSON file, no backend

`serve` claims a job only when the GPU has at least --min-free-vram-gb free:
on a shared GPU, claiming a job that then OOMs wastes an attempt.
"""
import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

from forge_runner import gpu
from forge_runner.client import BackendClient, OfflineClient
from forge_runner.engine import CommandEngine, DockerEngine
from forge_runner.job import Job
from forge_runner.supervisor import RunResult, run_job

# The training engine, pinned by digest. In `serve` the backend's pin
# (finetune_contract.ENGINE_IMAGE, sent as the poll payload's engine_image)
# wins; this default covers `run-local` and a payload without one.
ENGINE_IMAGE = ("hiyouga/llamafactory@sha256:"
                "c5e769e047a19f9322800c9881a6270557ef0e6682f0145b50b322f8724de6b6")
ENGINE_VERSION = "0.9.5"
HEARTBEAT_SECONDS = 30

logger = logging.getLogger("forge_runner")


def _engine(args, job: Optional[Job] = None):
    if args.engine_cmd:
        return CommandEngine(args.engine_cmd.split(), version=ENGINE_VERSION)
    image = (job.engine_image if job else None) or args.image
    return DockerEngine(image, Path(args.hf_cache), version=ENGINE_VERSION)


def _throttled(fn, every: float):
    last = [0.0]

    def call():
        now = time.monotonic()
        if now - last[0] >= every:
            last[0] = now
            fn()
    return call


def serve(args) -> int:
    api_key = args.api_key or os.environ.get("FORGE_API_KEY")
    if not api_key:
        print("forge-runner: a worker API key is required (--api-key or FORGE_API_KEY)", file=sys.stderr)
        return 2
    client = BackendClient(args.backend_url, api_key)
    work_root = Path(args.work_dir)
    work_root.mkdir(parents=True, exist_ok=True)

    worker_id = client.register(args.hostname or gpu.hostname(), gpu.describe())
    logger.info("Registered as worker %s; polling %s", worker_id, args.backend_url)

    def beat(busy: bool, job_id=None):
        total, free = gpu.memory_gb()
        client.heartbeat(worker_id, busy=busy, job_id=job_id,
                         vram_total_gb=total or 0.0, vram_free_gb=free or 0.0)

    idle_beat = _throttled(lambda: beat(False), HEARTBEAT_SECONDS)
    try:
        while True:
            idle_beat()
            _total, free = gpu.memory_gb()
            if free is not None and free < args.min_free_vram_gb:
                logger.info("Only %.1f GB VRAM free (< %.1f); not claiming a job", free, args.min_free_vram_gb)
                time.sleep(args.poll_interval)
                continue
            try:
                payload = client.poll(worker_id)
            except Exception as exc:
                logger.warning("Poll failed: %s", exc)
                time.sleep(args.poll_interval)
                continue
            if not payload:
                time.sleep(args.poll_interval)
                continue
            run_claimed(payload, client, lambda job: _engine(args, job), work_root, worker_id,
                        heartbeat_for=lambda job_id: _throttled(
                            lambda: beat(True, job_id), HEARTBEAT_SECONDS))
    except KeyboardInterrupt:
        logger.info("Stopping")
    finally:
        client.close()
    return 0


def run_claimed(payload: dict, client, make_engine, work_root: Path, worker_id: str,
                heartbeat_for=lambda job_id: None) -> Optional[RunResult]:
    """Run one claimed job so that one bad job costs one job, not the runner.

    run_job reports the failures it anticipates; anything else that escapes
    (a bad payload, a client bug) is reported here as runner_error, and the
    serve loop goes back to polling instead of leaving the job in `running`
    until the 10-minute runner_lost sweep.
    """
    job_id = payload.get("job_id")
    try:
        job = Job.from_payload(payload)
        logger.info("Claimed job %s (base %s)", job.job_id, job.source_ref)
        result = run_job(job, client, make_engine(job), work_root, worker_id,
                         heartbeat=heartbeat_for(job.job_id))
        logger.info("Job %s finished: %s %s", job.job_id, result.status, result.detail)
        return result
    except Exception as exc:
        logger.exception("Job %s crashed the supervisor", job_id)
        if job_id:
            client.report_failure(job_id, worker_id, error=f"forge-runner error: {exc}",
                                  code="runner_error", retryable=True)
        return None


def run_local(args) -> int:
    payload = json.loads(Path(args.job).read_text())
    payload.setdefault("dataset_path", "local:train")
    if args.validation:
        payload["validation_file_id"] = "local-validation"
    job = Job.from_payload(payload)
    files = {job.dataset_path: Path(args.train)}
    if args.validation:
        files["/v1/files/local-validation/content"] = Path(args.validation)
    work_root = Path(args.work_dir)
    work_root.mkdir(parents=True, exist_ok=True)
    client = OfflineClient(files, results_dir=work_root)
    result = run_job(job, client, _engine(args), work_root, worker_id="local")
    print(json.dumps({"result": result.status, **(client.outcome or {})}, indent=2, default=str))
    return 0 if result.status == "succeeded" else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="forge-runner")
    sub = parser.add_subparsers(dest="cmd", required=True)

    def engine_flags(p):
        p.add_argument("--work-dir", default=os.path.expanduser("~/forge-runner/jobs"))
        p.add_argument("--image", default=ENGINE_IMAGE, help="pinned LLaMA-Factory image")
        p.add_argument("--hf-cache", default=os.environ.get("HF_HOME", os.path.expanduser("~/.cache/huggingface")))
        p.add_argument("--engine-cmd", help="run this instead of docker; '{config}' is replaced by the config path")
        p.add_argument("-v", "--verbose", action="store_true")

    s = sub.add_parser("serve", help="poll Sheshnag and run finetune jobs")
    s.add_argument("--backend-url", default=os.environ.get("FORGE_BACKEND_URL", "http://localhost:8000"))
    s.add_argument("--api-key", help="worker gk- key (or FORGE_API_KEY)")
    s.add_argument("--hostname")
    s.add_argument("--poll-interval", type=float, default=10.0)
    s.add_argument("--min-free-vram-gb", type=float, default=4.5)
    engine_flags(s)

    r = sub.add_parser("run-local", help="run one job from a JSON file, no backend")
    r.add_argument("--job", required=True, help="JSON in the /workers/finetune-poll payload shape")
    r.add_argument("--train", required=True)
    r.add_argument("--validation")
    engine_flags(r)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return serve(args) if args.cmd == "serve" else run_local(args)


if __name__ == "__main__":
    sys.exit(main())
