"""
Configuration management for the GPU Worker Daemon.

Supports three layers of configuration (in order of priority):
    1. CLI arguments (highest)
    2. Environment variables (prefix: DAEMON_)
    3. YAML config file (lowest)

This design allows the same daemon binary to be configured differently
across dev, staging, and production without code changes.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import field
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field


def _generate_worker_id() -> str:
    """Generate a unique but human-readable worker identifier."""
    return f"worker-{uuid.uuid4().hex[:8]}"


class DaemonConfig(BaseModel):
    """
    Immutable configuration for a single daemon instance.

    Attributes:
        worker_id:      Unique identifier for this worker. Auto-generated if not provided.
        backend_url:    Base URL of the control plane API.
        vllm_url:       Base URL of the local vLLM OpenAI-compatible server.
        poll_interval:  Seconds between job poll attempts when idle.
        log_level:      Python logging level (DEBUG, INFO, WARNING, ERROR).
        work_dir:       Local directory for job artifacts (inputs, outputs).
    """

    worker_id: str = Field(default_factory=_generate_worker_id)
    backend_url: str = "http://localhost:8000"
    vllm_url: str = "http://localhost:8100"
    poll_interval: int = 5
    log_level: str = "INFO"
    work_dir: str = Field(default_factory=lambda: str(Path.home() / ".gpu-daemon" / "jobs"))

    # ── Future-proofing slots (Week 2+) ──────────────────────────
    # These fields exist so the config schema doesn't break when
    # features are added later. They are NOT used in Week 1.
    heartbeat_interval: Optional[int] = None
    checkpoint_interval: Optional[int] = None

    @classmethod
    def from_yaml(cls, path: str | Path) -> DaemonConfig:
        """
        Load configuration from a YAML file.

        Missing keys fall back to defaults. Extra keys are ignored
        to maintain forward compatibility.
        """
        config_path = Path(path)
        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        with open(config_path, "r") as fh:
            raw = yaml.safe_load(fh) or {}

        return cls(**raw)

    @classmethod
    def from_env(cls) -> DaemonConfig:
        """
        Build config from environment variables prefixed with DAEMON_.

        Example:
            DAEMON_BACKEND_URL=http://api.example.com
            DAEMON_POLL_INTERVAL=10
        """
        env_map = {
            "worker_id": os.getenv("DAEMON_WORKER_ID"),
            "backend_url": os.getenv("DAEMON_BACKEND_URL"),
            "vllm_url": os.getenv("DAEMON_VLLM_URL"),
            "poll_interval": os.getenv("DAEMON_POLL_INTERVAL"),
            "log_level": os.getenv("DAEMON_LOG_LEVEL"),
            "work_dir": os.getenv("DAEMON_WORK_DIR"),
        }
        # Filter out None values so defaults apply
        filtered = {k: v for k, v in env_map.items() if v is not None}

        # Cast numeric fields
        if "poll_interval" in filtered:
            filtered["poll_interval"] = int(filtered["poll_interval"])

        return cls(**filtered)

    @classmethod
    def load(cls, config_path: Optional[str] = None) -> DaemonConfig:
        """
        Smart loader: YAML file → env overrides → defaults.

        This is the primary entry point for loading config. It merges
        all three sources with correct precedence.
        """
        # Start with defaults
        base = {}

        # Layer 1: YAML file
        if config_path and Path(config_path).exists():
            with open(config_path, "r") as fh:
                yaml_data = yaml.safe_load(fh) or {}
            base.update(yaml_data)

        # Layer 2: Environment variables override YAML
        env_overrides = {
            "worker_id": os.getenv("DAEMON_WORKER_ID"),
            "backend_url": os.getenv("DAEMON_BACKEND_URL"),
            "vllm_url": os.getenv("DAEMON_VLLM_URL"),
            "poll_interval": os.getenv("DAEMON_POLL_INTERVAL"),
            "log_level": os.getenv("DAEMON_LOG_LEVEL"),
            "work_dir": os.getenv("DAEMON_WORK_DIR"),
        }
        for key, value in env_overrides.items():
            if value is not None:
                if key == "poll_interval":
                    base[key] = int(value)
                else:
                    base[key] = value

        return cls(**base)
