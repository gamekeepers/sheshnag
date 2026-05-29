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
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ── Enums ────────────────────────────────────────────────────────


class JobStatus(str, Enum):
    """
    Job lifecycle states.

    The daemon only cares about transitions it can trigger:
        assigned → running → completed | failed
    Other states (queued) are managed by the backend.
    """

    QUEUED = "queued"
    ASSIGNED = "assigned"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


# ── Job Model ────────────────────────────────────────────────────


class Job(BaseModel):
    """
    Represents a batch inference job received from the control plane.

    This is the daemon's view of a job — it contains only the fields
    the daemon needs to execute the job, not the full backend model.
    """

    job_id: str
    model: str = ""
    status: JobStatus = JobStatus.ASSIGNED
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
    def usage(self) -> Dict[str, int]:
        """
        Extract token usage from the response.

        Returns empty dict if usage data is not available.
        Useful for billing/tracking in future weeks.
        """
        if self.response and "usage" in self.response:
            return self.response["usage"]
        return {}
