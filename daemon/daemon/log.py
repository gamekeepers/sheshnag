"""
Logging configuration for the GPU Worker Daemon.

Provides structured, human-readable logs with timestamps and module context.
Designed to be extended with JSON logging / log aggregation in future weeks.
"""

from __future__ import annotations

import logging
import sys


_LOG_FORMAT = (
    "%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s"
)
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_initialized = False


def setup_logging(level: str = "INFO") -> None:
    """
    Configure the root logger for the daemon process.

    Should be called once at startup. Subsequent calls are no-ops
    to prevent duplicate handlers.

    Args:
        level: Python logging level name (DEBUG, INFO, WARNING, ERROR).
    """
    global _initialized
    if _initialized:
        return

    numeric_level = getattr(logging, level.upper(), logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(numeric_level)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))

    root = logging.getLogger("daemon")
    root.setLevel(numeric_level)
    root.addHandler(handler)

    # Suppress noisy third-party loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    _initialized = True


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger scoped under the daemon namespace.

    Usage:
        logger = get_logger(__name__)
        logger.info("Starting worker...")

    Args:
        name: Typically __name__ of the calling module.

    Returns:
        A Logger instance under the 'daemon' hierarchy.
    """
    # Ensure all daemon loggers are children of the root 'daemon' logger
    if not name.startswith("daemon"):
        name = f"daemon.{name}"
    return logging.getLogger(name)
