"""
CLI entry point for the GPU Worker Daemon.

Usage:
    # With config file
    python -m daemon.main --config config.yaml

    # With CLI overrides
    python -m daemon.main --backend-url http://api.example.com --worker-id my-worker

    # With environment variables
    DAEMON_BACKEND_URL=http://api.example.com python -m daemon.main

    # With authentication
    python -m daemon.main --api-key my-secret-key

Configuration precedence (highest → lowest):
    1. CLI arguments
    2. Environment variables
    3. YAML config file
    4. Defaults
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from typing import Any, Dict, List, Optional

from daemon import __version__
from daemon.client import BackendClient
from daemon.config import DaemonConfig
from daemon.executors.base import BaseExecutor
from daemon.executor_factory import create_executors
from daemon.log import get_logger, setup_logging
from daemon.models import WorkerRuntimeBundle
from daemon.registration import RegistrationManager
from daemon.worker import Worker


def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="gpu-daemon",
        description="GPU Worker Daemon — polls for batch inference jobs and executes them via vLLM",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python -m daemon.main --config config.yaml\n"
            "  python -m daemon.main --backend-url http://localhost:8000\n"
            "  python -m daemon.main --worker-id my-gpu-01 --vllm-url http://localhost:8100\n"
            "  python -m daemon.main --api-key my-secret-key --gpu-name 'RTX 4090'\n"
        ),
    )

    parser.add_argument(
        "--version", action="version", version=f"gpu-daemon {__version__}"
    )

    parser.add_argument(
        "-c", "--config",
        type=str,
        default=None,
        help="Path to YAML config file",
    )

    parser.add_argument(
        "--backend-url",
        type=str,
        default=None,
        help="Control plane API URL (default: http://localhost:8000)",
    )

    parser.add_argument(
        "--vllm-url",
        type=str,
        default=None,
        help="vLLM server URL (default: http://localhost:8100)",
    )
    
    parser.add_argument(
        "--ollama-url",
        type=str,
        default=None,
        help="Ollama server URL (default: http://localhost:11434)",
    )

    parser.add_argument(
        "--worker-id",
        type=str,
        default=None,
        help="Unique worker ID (default: auto-generated with hostname)",
    )

    parser.add_argument(
        "--poll-interval",
        type=int,
        default=None,
        help="Seconds between poll attempts (default: 5)",
    )
    
    parser.add_argument(
        "--heartbeat-interval",
        type=int,
        default=None,
        help="Seconds between heartbeats (default: 30)",
    )

    parser.add_argument(
        "--log-level",
        type=str,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default=None,
        help="Logging level (default: INFO)",
    )

    parser.add_argument(
        "--work-dir",
        type=str,
        default=None,
        help="Directory for job artifacts (default: ~/.gpu-daemon/jobs)",
    )

    # ── Authentication (Spec §17) ────────────────────────────────

    parser.add_argument(
        "--api-key",
        type=str,
        default=None,
        help="Org worker API key (Bearer token) — created in the platform dashboard",
    )

    # ── Registration metadata (Spec §8) ──────────────────────────

    parser.add_argument(
        "--gpu-name",
        type=str,
        default=None,
        help="GPU model name for registration (e.g., 'RTX 4090')",
    )

    parser.add_argument(
        "--vram-gb",
        type=float,
        default=None,
        help="GPU VRAM in GB for registration (e.g., 24.0)",
    )

    parser.add_argument(
        "--models",
        type=str,
        nargs="+",
        default=None,
        help="Model names available on this worker (space-separated)",
    )

    parser.add_argument(
        "--runtime",
        type=str,
        nargs="+",
        action="append",
        default=None,
        help="Inference runtime(s) to drive, e.g. --runtime ollama --runtime vllm "
              "or --runtime vllm ollama (default: ollama)",
    )

    # ── Executor tuning ──────────────────────────────────────────

    parser.add_argument(
        "--inference-timeout",
        type=float,
        default=None,
        help="Per-prompt inference timeout in seconds (default: 300.0)",
    )

    parser.add_argument(
        "--max-concurrent-prompts",
        type=int,
        default=None,
        help="Prompts executed concurrently per job (default: 8)",
    )

    return parser


def _build_cli_overrides(args: argparse.Namespace) -> dict:
    """
    Extract CLI arguments into a dict for config override.

    Only includes arguments that were explicitly set (not None).
    This dict is passed to DaemonConfig.load(cli_overrides=...)
    so all precedence logic lives in one place.
    """
    # --runtime is action="append" + nargs="+": args.runtime holds one
    # group per flag occurrence. Flatten it so `--runtime vllm ollama`
    # and `--runtime vllm --runtime ollama` both land on the same plain
    # list (the repeated form used to be silently clobbered by the
    # store action).
    runtime = (
        [r for group in args.runtime for r in group]
        if args.runtime is not None
        else None
    )

    mapping = {
        "backend_url": args.backend_url,
        "vllm_url": args.vllm_url,
        "ollama_url": args.ollama_url,
        "worker_id": args.worker_id,
        "poll_interval": args.poll_interval,
        "heartbeat_interval": args.heartbeat_interval,
        "log_level": args.log_level,
        "work_dir": args.work_dir,
        "api_key": args.api_key,
        "gpu_name": args.gpu_name,
        "vram_gb": args.vram_gb,
        "models": args.models,
        "runtime": runtime,
        "inference_timeout": args.inference_timeout,
        "max_concurrent_prompts": args.max_concurrent_prompts,
    }

    # Filter out None values — DaemonConfig.load() also does this,
    # but pre-filtering keeps the dict clean for debugging.
    return {k: v for k, v in mapping.items() if v is not None}


async def _collect_runtime_bundles(
    config: DaemonConfig,
    executors: Dict[str, BaseExecutor],
    ready: Optional[List[str]] = None,
) -> List[WorkerRuntimeBundle]:
    """One WorkerRuntimeBundle per configured runtime, unconditionally.

    Readiness never drops a runtime from the payload: the backend's
    replace-all register deletes any worker_runtimes row missing from
    it (and every runtime_models row under it), and nothing re-registers
    a dropped runtime. A runtime that is still loading is advertised
    with whatever it can report — empty models/inventory when it can't
    be queried — and heartbeats fill its row in once it recovers. It
    carries status="unavailable", which is what keeps its models out of
    dispatch — they stay in the catalogue, because a model on disk is a
    real fact about this worker and survives the runtime being down.
    ready=None means readiness was never established, and every runtime
    keeps the default.

    On a mixed node the flat config.models list is split per bundle:
    each entry is advertised by the runtime that reports it in
    list_models() — the same source the routing map uses — and entries
    no runtime claims are logged, never discarded silently.
    """
    targets = list(executors)

    logger = get_logger(__name__)

    # Which runtime reports which configured model (mixed nodes only —
    # a single runtime's list is unambiguous and advertised whole).
    # A failed query counts as reporting nothing: its entries surface
    # in the unclaimed warning below rather than vanishing silently.
    reported: Dict[str, set] = {}
    if len(config.runtime) > 1 and config.models:
        for name in targets:
            executor = executors[name]
            if not hasattr(executor, "list_models"):
                continue
            try:
                reported[name] = set(await executor.list_models())
            except Exception as exc:
                logger.debug(f"Could not list models from runtime '{name}': {exc}")
                reported[name] = set()

    bundles: List[WorkerRuntimeBundle] = []
    for name in targets:
        executor = executors[name]
        status = "ready" if ready is None or name in ready else "unavailable"
        # Best-effort provenance for the advertised models; empty when
        # the runtime can't be queried (or can't report digests at all).
        digests: Dict[str, Any] = {}
        if hasattr(executor, "list_models_detailed"):
            try:
                digests = {
                    m["name"]: m.get("digest")
                    for m in await executor.list_models_detailed()
                    if m.get("name")
                }
            except Exception as exc:
                logger.debug(f"Could not collect digests from runtime '{name}': {exc}")

        # First full on-disk inventory (artifact file hashes) — never
        # raises, [] when the runtime or its models dir isn't reachable.
        try:
            inventory = await executor.inventory()
        except Exception as exc:
            logger.debug(f"Could not collect inventory from runtime '{name}': {exc}")
            inventory = []

        names = list(digests.keys())
        if len(config.runtime) == 1:
            # Single runtime: the static config.models list is
            # unambiguous and was always advertised alongside the
            # dynamic list — keep it, deduped.
            names = list(dict.fromkeys(list(config.models) + names))
        else:
            # Mixed node: advertise the configured entries this
            # runtime reports, deduped against the dynamic names.
            mine = reported.get(name)
            if mine:
                names = list(dict.fromkeys(
                    [m for m in config.models if m in mine] + names
                ))

        bundles.append(WorkerRuntimeBundle(
            runtime=name,
            status=status,
            loads_on_demand=getattr(executor, "loads_on_demand", True),
            models=names,
            model_digests=digests,
            inventory=inventory,
        ))

    if len(config.runtime) > 1 and config.models:
        claimed: set = set()
        for models in reported.values():
            claimed |= models
        unclaimed = [m for m in dict.fromkeys(config.models) if m not in claimed]
        if unclaimed:
            logger.warning(
                "config.models entries not advertised — no configured "
                "runtime reports them: " + ", ".join(unclaimed)
            )
    return bundles


async def _run(config: DaemonConfig) -> None:
    logger = get_logger(__name__)
    """
    Async entry point — wires up all components and starts the worker.

    Component creation follows Dependency Injection:
        Config → Client + Executors → Worker

    Startup sequence:
        1. Create components (one executor per configured runtime)
        2. Wait for the runtimes to become healthy (concurrently)
        3. Register the configured runtimes with the control plane (spec §8)
        4. Start the poll-execute loop
    """
    # ── Resolve the org worker API key  ───────────
    # The key is created in the platform dashboard and is a required
    # input — the backend authenticates every /workers/* call with it
    # and never issues keys itself.
    reg_manager = RegistrationManager(config.credentials_path)
    saved_key = reg_manager.load_saved_credentials()

    api_key = config.api_key or saved_key
    if not api_key:
        logger.error(
            "No API key configured. Create an org worker API key in the "
            "platform dashboard and pass it via --api-key, DAEMON_API_KEY, "
            "or api_key in config.yaml."
        )
        sys.exit(1)
    config.api_key = api_key

    # ── Create components ────────────────────────────────────────
    client = BackendClient(
        base_url=config.backend_url,
        worker_id=config.worker_id,
        api_key=api_key,
    )
    # One executor per configured runtime, built before registration so
    # we can advertise per-runtime models/digests/inventory at register
    # time, not only via heartbeats.
    executors = create_executors(config)
    worker = Worker(
        config=config,
        client=client,
        executors=executors,
    )

    # ── Wait for the runtimes to be ready ─────────────────────────
    # Concurrently, 60s max per runtime. Down runtimes are not fatal —
    # they are registered too, and heartbeats fill their rows in once
    # they recover.
    ready_runtimes = await worker.wait_for_runtimes()

    # ── Register with platform ───────────────────────────────────
    bundles = await _collect_runtime_bundles(config, executors, ready_runtimes)
    try:
        assigned_worker_id = await reg_manager.register(client, config, bundles)
        config.worker_id = assigned_worker_id
        client.update_worker_id(assigned_worker_id)
        worker.update_worker_id(assigned_worker_id)
        logger.info(f"Worker registered: {assigned_worker_id}")
    except Exception as exc:
        logger.error(f"Failed to register with platform: {exc}")
        saved_worker_id = reg_manager.load_saved_worker_id()
        if not saved_worker_id:
            logger.error("No previously assigned worker id available. Exiting.")
            sys.exit(1)
        config.worker_id = saved_worker_id
        client.update_worker_id(saved_worker_id)
        worker.update_worker_id(saved_worker_id)
        logger.warning(
            f"Continuing as previously registered worker "
            f"'{saved_worker_id}' despite registration failure."
        )

    # ── Run ──────────────────────────────────────────────────────
    try:
        await worker.start()
    finally:
        await worker.shutdown()


def main() -> None:
    """CLI entry point."""
    parser = _build_parser()
    args = parser.parse_args()

    # Build CLI overrides dict
    cli_overrides = _build_cli_overrides(args)

    # Load config with consolidated precedence: YAML → env → CLI
    config = DaemonConfig.load(
        config_path=args.config,
        cli_overrides=cli_overrides,
    )

    # Setup logging (must happen before any log calls)
    setup_logging(config.log_level)
    logger = get_logger(__name__)

    # Banner
    auth_status = "✓ configured" if config.api_key else "✗ not configured"
    models_str = ", ".join(config.models) if config.models else "(none)"

    logger.info(f"{'='*60}")
    logger.info(f"  GPU Worker Daemon v{__version__}")
    logger.info(f"  Worker ID:     {config.worker_id}")
    logger.info(f"  Backend URL:   {config.backend_url}")
    logger.info(f"  Ollama URL:    {config.ollama_url}")
    logger.info(f"  vLLM URL:      {config.vllm_url}")
    logger.info(f"  Poll interval: {config.poll_interval}s")
    logger.info(f"  Heartbeat:     {config.heartbeat_interval}s")
    logger.info(f"  Work dir:      {config.work_dir}")
    logger.info(f"  Auth:          {auth_status}")
    logger.info(f"  Models:        {models_str}")
    logger.info(f"  Runtimes:      {', '.join(config.runtime)}")
    logger.info(f"  Concurrency:   {config.max_concurrent_prompts}")
    logger.info(f"{'='*60}")

    # Run the async event loop
    # Signal handlers in worker.py handle SIGINT/SIGTERM gracefully,
    # so we only need a minimal KeyboardInterrupt catch here as a
    # fallback for edge cases (e.g., interrupt before loop starts).
    try:
        asyncio.run(_run(config))
    except KeyboardInterrupt:
        pass  # Signal handler already logged the shutdown


if __name__ == "__main__":
    main()
