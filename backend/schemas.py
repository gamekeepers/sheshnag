from pydantic import Field, BaseModel
from typing import Literal, Optional, List


class FileOut(BaseModel):
    id: str
    object: str = "file"
    bytes: int
    created_at: int
    filename: str
    purpose: str

    class Config:
        from_attributes = True


class RequestCounts(BaseModel):
    total: int = 0
    completed: int = 0
    failed: int = 0


class BatchOut(BaseModel):
    id: str
    object: str = "batch"
    endpoint: str
    model: Optional[str] = None
    input_file_id: str
    completion_window: str
    status: str
    output_file_id: Optional[str] = None
    error_file_id: Optional[str] = None
    error_details: Optional[str] = None
    created_at: int
    expires_at: Optional[int] = None
    requested_at: Optional[int] = None
    completed_at: Optional[int] = None
    request_counts: RequestCounts = RequestCounts()

    @classmethod
    def from_batch(cls, batch):
        return cls(
            id=batch.id,
            endpoint=batch.endpoint,
            model=batch.model,
            input_file_id=batch.input_file_id,
            completion_window=batch.completion_window,
            status=batch.status,
            output_file_id=batch.output_file_id,
            error_file_id=batch.error_file_id,
            error_details=batch.error_details,
            created_at=batch.created_at,
            expires_at=batch.expires_at,
            requested_at=batch.requested_at,
            completed_at=batch.completed_at,
            request_counts=RequestCounts(
                total=batch.request_counts_total or 0,
                completed=batch.request_counts_completed or 0,
                failed=batch.request_counts_failed or 0,
            ),
        )


class BatchSummary(BaseModel):
    """Provider view — no input_file_id or prompt content."""
    id: str
    object: str = "batch"
    endpoint: str
    model: Optional[str] = None
    status: str
    created_at: int
    completed_at: Optional[int] = None
    request_counts: RequestCounts = RequestCounts()

    @classmethod
    def from_batch(cls, batch):
        return cls(
            id=batch.id,
            endpoint=batch.endpoint,
            model=batch.model,
            status=batch.status,
            created_at=batch.created_at,
            completed_at=batch.completed_at,
            request_counts=RequestCounts(
                total=batch.request_counts_total or 0,
                completed=batch.request_counts_completed or 0,
                failed=batch.request_counts_failed or 0,
            ),
        )


class BatchCreate(BaseModel):
    input_file_id: str
    endpoint: str
    completion_window: str = "24h"


class SignupRequest(BaseModel):
    email: str
    password: str
    full_name: str


class LoginRequest(BaseModel):
    email: str
    password: str


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


class UserOut(BaseModel):
    id: str
    email: str
    full_name: str
    platform_role: str
    is_active: bool
    must_change_password: bool
    created_at: int

    class Config:
        from_attributes = True


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    platform_role: str = "user"
    must_change_password: bool = False


class WorkerHeartbeatRequest(BaseModel):
    """Unified worker heartbeat (spec §8.1: dynamic properties).

    `status` is the daemon's activity (idle | busy | downloading_model);
    liveness (`online`/`offline`) is derived server-side from last_heartbeat.
    """
    status: str = "idle"
    current_job_id: Optional[str] = None
    progress: Optional[dict] = None
    gpu_utilization: float = 0.0
    gpu_memory_used_gb: float = 0.0
    vram_total_gb: float = 0.0
    vram_available_gb: float = 0.0
    loaded_models: List[str] = []
    uptime_seconds: int = 0


class ForgotPasswordRequest(BaseModel):
    email: str


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


# ─── Worker schemas ────────────────────────────────────────

class GpuInfo(BaseModel):
    index: int = 0
    vendor: str = "nvidia"
    name: str
    vram_gb: float
    driver: Optional[str] = None
    cuda: Optional[str] = None


class RuntimeInfo(BaseModel):
    type: str                   # "ollama", "vllm", etc.
    endpoint: str
    models: List[str] = []


class WorkerRegisterRequest(BaseModel):
    hostname: str
    os: Optional[str] = None
    cpu: Optional[dict] = None
    ram: Optional[dict] = None
    gpus: List[GpuInfo] = []
    runtimes: List[RuntimeInfo] = []


# ─── Personal API Key schemas ──────────────────────────────

class ApiKeyCreate(BaseModel):
    name: str
    expires_at: Optional[int] = Field(
        None,
        description="Unix timestamp. Must be in the future, max 1 year out.",
    )


class ApiKeyUpdate(BaseModel):
    name: Optional[str] = None
    expires_at: Optional[int] = Field(
        None,
        description="Unix timestamp. Must be in the future, max 1 year out.",
    )
    status: Optional[Literal["active", "revoked"]] = None  # validated at schema level


class ApiKeyOut(BaseModel):
    id: str
    name: Optional[str] = None
    key_prefix: str
    status: str
    key_type: str = "personal"
    expires_at: Optional[int] = None
    last_used_at: Optional[int] = None
    created_at: int

    class Config:
        from_attributes = True



