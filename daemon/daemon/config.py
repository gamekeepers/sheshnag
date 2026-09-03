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
import socket
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel, Field


# ── Worker ID Generation ─────────────────────────────────────────


def _generate_worker_id() -> str:
    """
    Generate a unique but human-readable worker identifier.

    Includes the hostname for debuggability across restarts —
    when multiple workers are running, the hostname prefix makes
    it immediately obvious which machine a log line came from.
    """
    hostname = socket.gethostname()
    short_uuid = uuid.uuid4().hex[:8]
    return f"worker-{hostname}-{short_uuid}"


# ── Environment Variable Mapping ─────────────────────────────────
#
# Single source of truth for env var → config field mapping.
# Both from_env() and load() reference this, eliminating duplication.

_ENV_MAP: Dict[str, str] = {
    "worker_id": "DAEMON_WORKER_ID",
    "backend_url": "DAEMON_BACKEND_URL",
    "vllm_url": "DAEMON_VLLM_URL",
    "ollama_url": "DAEMON_OLLAMA_URL",
    "poll_interval": "DAEMON_POLL_INTERVAL",
    "log_level": "DAEMON_LOG_LEVEL",
    "work_dir": "DAEMON_WORK_DIR",
    "api_key": "DAEMON_API_KEY",
    "gpu_name": "DAEMON_GPU_NAME",
    "vram_gb": "DAEMON_VRAM_GB",
    "runtime": "DAEMON_RUNTIME",
    "inference_timeout": "DAEMON_INFERENCE_TIMEOUT",
    "heartbeat_interval": "DAEMON_HEARTBEAT_INTERVAL",
    "progress_interval_seconds": "DAEMON_PROGRESS_INTERVAL_SECONDS",
}

# Fields that need type coercion from string env vars
_INT_FIELDS = frozenset({"poll_interval", "heartbeat_interval"})
_FLOAT_FIELDS = frozenset({"vram_gb", "inference_timeout", "progress_interval_seconds"})


def _read_env() -> Dict[str, Any]:
    """
    Read configuration from environment variables.

    Returns a dict of field_name → value for all env vars that are set.
    Handles type coercion for numeric fields.
    """
    result: Dict[str, Any] = {}

    for field_name, env_var in _ENV_MAP.items():
        value = os.getenv(env_var)
        if value is None:
            continue

        # Type coercion for numeric fields
        if field_name in _INT_FIELDS:
            result[field_name] = int(value)
        elif field_name in _FLOAT_FIELDS:
            result[field_name] = float(value)
        else:
            result[field_name] = value

    # Special handling: DAEMON_MODELS is a comma-separated list
    models_env = os.getenv("DAEMON_MODELS")
    if models_env:
        result["models"] = [m.strip() for m in models_env.split(",") if m.strip()]

    return result


# ── Configuration Model ──────────────────────────────────────────


class DaemonConfig(BaseModel):
    """
    Immutable configuration for a single daemon instance.

    All fields have sensible defaults for local development.
    Production deployments should set values via YAML, env vars,
    or CLI arguments.

    Attributes:
        worker_id:      Unique identifier for this worker. Auto-generated if not provided.
        backend_url:    Base URL of the control plane API.
        vllm_url:       Base URL of the local vLLM OpenAI-compatible server.
        poll_interval:  Seconds between job poll attempts when idle (must be > 0).
        log_level:      Python logging level (DEBUG, INFO, WARNING, ERROR).
        work_dir:       Local directory for job artifacts (inputs, outputs).
        api_key:        Org worker API key for authentication (spec §8.0/§17).
                        Created in the platform dashboard; required to register.
        gpu_name:       Human-readable GPU model name for registration (spec §8).
        vram_gb:        Advertised GPU memory in GB. When > 0 it overrides
                        probing in both registration and every heartbeat.
        models:         List of model names available on this worker (spec §8).
        runtime:        Inference runtime type — "ollama" (default) or "vllm" .
        inference_timeout: Per-prompt inference timeout in seconds (any runtime).
    """

    worker_id: str = Field(default_factory=_generate_worker_id)
    backend_url: str = "http://localhost:8000"
    vllm_url: str = "http://localhost:8100"
    ollama_url: str = "http://localhost:11434"
    poll_interval: int = Field(default=5, gt=0, description="Seconds between poll attempts, must be > 0")
    log_level: str = "INFO"
    work_dir: str = Field(default_factory=lambda: str(Path.home() / ".gpu-daemon" / "jobs"))
    credentials_path: str = Field(default_factory=lambda: str(Path.home() / ".gpu-daemon" / "credentials"))

    # ── Authentication (Spec §17) ────────────────────────────────
    api_key: Optional[str] = None

    # ── Registration metadata (Spec §8) ──────────────────────────
    gpu_name: str = "unknown"
    vram_gb: float = Field(default=0.0, ge=0, description="GPU VRAM in GB, must be >= 0")
    models: List[str] = Field(default_factory=list)
    runtime: str = "ollama"

    # ── Executor tuning ──────────────────────────────────────────
    inference_timeout: float = Field(default=300.0, gt=0, description="Per-prompt timeout in seconds, must be > 0")

    # ── Heartbeats & Progress ────────────────────────────────────
    heartbeat_interval: int = Field(default=30, gt=0)
    progress_interval_seconds: float = Field(
        default=5.0, gt=0, description="Minimum seconds between progress reporting roundtrips"
    )

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
            DAEMON_API_KEY=my-secret-key
            DAEMON_MODELS=model-a,model-b
        """
        return cls(**_read_env())

    @classmethod
    def load(
        cls,
        config_path: Optional[str] = None,
        cli_overrides: Optional[Dict[str, Any]] = None,
    ) -> DaemonConfig:
        """
        Smart loader: YAML file → env overrides → CLI → defaults.

        This is the primary entry point for loading config. It merges
        all sources with correct precedence in a single place.

        Args:
            config_path:    Optional path to a YAML config file.
            cli_overrides:  Optional dict of CLI argument overrides (highest priority).
                            None values are filtered out automatically.
        """
        # Start with empty base
        base: Dict[str, Any] = {}

        # Layer 1: YAML file (lowest priority)
        if config_path and Path(config_path).exists():
            with open(config_path, "r") as fh:
                yaml_data = yaml.safe_load(fh) or {}
            base.update(yaml_data)

        # Layer 2: Environment variables override YAML
        base.update(_read_env())

        # Layer 3: CLI overrides (highest priority)
        if cli_overrides:
            for key, value in cli_overrides.items():
                if value is not None:
                    base[key] = value

        return cls(**base)
