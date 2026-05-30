"""
Data models for the GPU Worker Daemon.

All data flowing through the daemon is validated via Pydantic models.
This ensures type safety at boundaries (HTTP responses, JSONL parsing)
and provides clear documentation of the expected data shapes.

Models are intentionally kept flat and simple for Week 1.
Nested/complex models can be introduced as the protocol evolves.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field


# ── Enums ────────────────────────────────────────────────────────


class JobStatus(str, Enum):
    """
    Job lifecycle states as defined in the spec.

    State machine:  queued → running → completed | failed

    The daemon triggers:
        running → completed  (on success)
        running → failed     (on error)
    The backend manages:
        queued → running     (on assignment via poll)
    """

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


# ── Worker Registration (Spec §8) ───────────────────────────────


class WorkerInfo(BaseModel):
    """
    Worker registration payload sent to the control plane.

    Per spec §8, a worker must register with metadata before polling.
    This model captures the worker's identity, hardware capabilities,
    and available models so the scheduler can make informed assignments.

    Attributes:
        worker_id: Unique identifier for this worker instance.
        gpu_name:  Human-readable GPU model (e.g., "RTX 4090").
        vram_gb:   GPU VRAM in gigabytes.
        models:    List of model names available on this worker.
        runtime:   Inference runtime type (e.g., "vllm").
        status:    Current worker status ("online", "offline", "busy").
    """

    worker_id: str
    gpu_name: str = "unknown"
    vram_gb: float = 0.0
    models: List[str] = Field(default_factory=list)
    runtime: str = "vllm"
    status: str = "online"


# ── Job Model ────────────────────────────────────────────────────


class Job(BaseModel):
    """
    Represents a batch inference job received from the control plane.

    This is the daemon's view of a job — it contains only the fields
    the daemon needs to execute the job, not the full backend model.

    Attributes:
        job_id:        Unique job identifier.
        input_file_id: ID of the uploaded input file (OpenAI batch format).
        input_path:    Backend-provided URL path to download the input file
                       (e.g., "/v1/files/{file_id}/content").
        model:         Model name to use for inference.
        input_file:    (Legacy) Name of the input JSONL file.
        status:        Current job status (trusted from backend).
        max_tokens:    Default max tokens for prompts in this job.
        temperature:   Default temperature for prompts in this job.
    """

    job_id: str
    input_file_id: Optional[str] = None
    input_path: Optional[str] = None
    model: str = ""
    input_file: Optional[str] = None
    status: JobStatus = JobStatus.QUEUED
    max_tokens: int = 512
    temperature: float = 0.7


# ── Poll Response ────────────────────────────────────────────────


class PollResponse(BaseModel):
    """
    Response from POST /workers/poll.

    When no job is available, `job` is None (or the backend returns 204).
    """

    job: Optional[Job] = None


# ── JSONL Prompt (Input) ─────────────────────────────────────────


class PromptRequest(BaseModel):
    """
    A single row from the input JSONL file.

    Follows the OpenAI batch API format:
        {"custom_id": "...", "method": "POST", "url": "/v1/chat/completions", "body": {...}}

    The `body` dict is passed directly to the vLLM OpenAI-compatible endpoint.
    """

    custom_id: str
    method: str = "POST"
    url: str = "/v1/chat/completions"
    body: Dict[str, Any] = Field(default_factory=dict)


# ── Completion Result (Output) ───────────────────────────────────


class CompletionResult(BaseModel):
    """
    A single row in the output JSONL file.

    Contains the original custom_id for correlation, the full API
    response body from vLLM, and an optional error message.

    The `response` field mirrors the OpenAI chat completion response
    structure so downstream consumers can parse it identically.
    """

    custom_id: str
    response: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

    @property
    def is_success(self) -> bool:
        """Check if this result completed without errors."""
        return self.error is None and self.response is not None

    @property
    def usage(self) -> Dict[str, Union[int, float]]:
        """
        Extract token usage from the response.

        Returns empty dict if usage data is not available.
        Useful for billing/tracking in future weeks.

        Note: Some providers return float values for certain usage
        fields, so we accept both int and float.
        """
        if self.response and "usage" in self.response:
            return self.response["usage"]
        return {}
