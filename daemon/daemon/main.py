"""
CLI entry point for the GPU Worker Daemon.

Usage:
    # With config file
    python -m daemon.main --config config.yaml

    # With CLI overrides
    python -m daemon.main --backend-url http://api.example.com --worker-id my-worker

    # With environment variables
    DAEMON_BACKEND_URL=http://api.example.com python -m daemon.main

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

from daemon import __version__
from daemon.client import BackendClient
from daemon.config import DaemonConfig
from daemon.executors.vllm import VLLMExecutor
from daemon.log import get_logger, setup_logging
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
        "--worker-id",
        type=str,
        default=None,
        help="Unique worker ID (default: auto-generated)",
    )

    parser.add_argument(
        "--poll-interval",
        type=int,
        default=None,
        help="Seconds between poll attempts (default: 5)",
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

    return parser


def _load_config(args: argparse.Namespace) -> DaemonConfig:
    """
    Build config by merging YAML + env + CLI with correct precedence.

    CLI arguments override everything. This lets operators deploy with
    a shared config file but override per-worker settings via flags.
    """
    # Load base config from YAML + env
    config = DaemonConfig.load(config_path=args.config)

    # Layer 3: CLI overrides (highest priority)
    cli_overrides = {}
    if args.backend_url is not None:
        cli_overrides["backend_url"] = args.backend_url
    if args.vllm_url is not None:
        cli_overrides["vllm_url"] = args.vllm_url
    if args.worker_id is not None:
        cli_overrides["worker_id"] = args.worker_id
    if args.poll_interval is not None:
        cli_overrides["poll_interval"] = args.poll_interval
    if args.log_level is not None:
        cli_overrides["log_level"] = args.log_level
    if args.work_dir is not None:
        cli_overrides["work_dir"] = args.work_dir

    if cli_overrides:
        # Create new config with overrides applied
        config = config.model_copy(update=cli_overrides)

    return config


async def _run(config: DaemonConfig) -> None:
    """
    Async entry point — wires up all components and starts the worker.

    Component creation follows Dependency Injection:
        Config → Client + Executor → Worker
    This keeps everything testable and loosely coupled.
    """
    # ── Create components ────────────────────────────────────────
    client = BackendClient(
        base_url=config.backend_url,
        worker_id=config.worker_id,
    )

    executor = VLLMExecutor(
        base_url=config.vllm_url,
    )

    worker = Worker(
        config=config,
        client=client,
        executor=executor,
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

    # Load config
    config = _load_config(args)

    # Setup logging (must happen before any log calls)
    setup_logging(config.log_level)
    logger = get_logger(__name__)

    # Banner
    logger.info(f"{'='*60}")
    logger.info(f"  GPU Worker Daemon v{__version__}")
    logger.info(f"  Worker ID:    {config.worker_id}")
    logger.info(f"  Backend URL:  {config.backend_url}")
    logger.info(f"  vLLM URL:     {config.vllm_url}")
    logger.info(f"  Poll interval: {config.poll_interval}s")
    logger.info(f"  Work dir:     {config.work_dir}")
    logger.info(f"{'='*60}")

    # Run the async event loop
    try:
        asyncio.run(_run(config))
    except KeyboardInterrupt:
        logger.info("Daemon interrupted by user")
        sys.exit(0)


if __name__ == "__main__":
    main()
