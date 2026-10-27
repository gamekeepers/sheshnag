from pydantic import AfterValidator, Field, BaseModel
from typing import Annotated, Literal, Optional, List


# bcrypt only hashes the first 72 bytes of a password. Storing the hash of a
# silently-truncated prefix is worse than refusing the input, so passwords that
# will be hashed are bounded at the edge.
BCRYPT_MAX_BYTES = 72


def _within_bcrypt_limit(value: str) -> str:
    if len(value.encode("utf-8")) > BCRYPT_MAX_BYTES:
        raise ValueError(f"password must be at most {BCRYPT_MAX_BYTES} bytes when UTF-8 encoded")
    return value


# max_length publishes the bound in the OpenAPI schema; the validator does the
# real check, because 72 characters can exceed 72 bytes once they aren't ASCII.
# Applied only to passwords being *set* — passwords being *checked* stay plain
# `str` so a wrong password keeps returning 401 rather than 422.
NewPassword = Annotated[
    str,
    Field(max_length=BCRYPT_MAX_BYTES),
    AfterValidator(_within_bcrypt_limit),
]


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
    password: NewPassword
    full_name: str


class LoginRequest(BaseModel):
    email: str
    password: str


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: NewPassword


class UserOut(BaseModel):
    id: str
    email: str
    full_name: str
    platform_role: str
    is_active: bool
    must_change_password: bool
    auth_provider: str = "local"
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

    `activity` is what the daemon is doing (idle | busy | downloading_model);
    liveness (`online`/`offline`/…) is a separate vocabulary managed
    server-side from heartbeat arrival and the sweeper timeout.
    """
    activity: Literal["idle", "busy", "downloading_model"] = "idle"
    current_job_id: Optional[str] = None
    progress: Optional[dict] = None
    gpu_utilization: float = 0.0
    gpu_memory_used_gb: float = 0.0
    vram_total_gb: float = 0.0
    vram_available_gb: float = 0.0
    loaded_models: List[str] = []
    # Optional name → digest map for the loaded models (additive; older
    # daemons omit it and fall back to name matching).
    loaded_model_digests: dict = {}
    uptime_seconds: int = 0


class ProgressReport(BaseModel):
    """Live batch progress from a worker (sent every N prompts)."""
    job_id: str
    worker_id: str
    completed: int = 0
    failed: int = 0
    total: int = 0


class ModelDownloadReport(BaseModel):
    """Model download progress from a worker (Ollama pull callback)."""
    worker_id: str
    model_name: str
    status: str = "downloading"
    completed: int = 0
    total: int = 0


class ForgotPasswordRequest(BaseModel):
    email: str


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: NewPassword


class AllowedDomainCreate(BaseModel):
    domain: str                          # bare domain, e.g. "dau.ac.in"
    include_subdomains: bool = False
    note: Optional[str] = None


class GoogleAuthRequest(BaseModel):
    id_token: str   # Google ID token from the frontend


class GoogleTokenOut(TokenOut):
    is_new_user: bool = False


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
    # Optional name → digest map (additive; older daemons omit it).
    model_digests: dict = {}


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


# ─── Finetuning schemas ─────────────────────────────────────

class FinetuneJobCreate(BaseModel):
    dataset_file_id: str
    base_model_id: str
    lora_rank: int = 8
    lora_alpha: int = 16
    lora_dropout: float = 0.05
    target_modules: Optional[str] = None  # comma-separated; None = executor default
    epochs: int = 3
    learning_rate: float = 2e-4
    micro_batch_size: int = 1
    max_seq_length: int = 512


class FinetuneProgress(BaseModel):
    current_step: Optional[int] = None
    total_steps: Optional[int] = None
    current_epoch: Optional[float] = None
    latest_loss: Optional[float] = None
    checkpoint_step: Optional[int] = None


class FinetuneJobOut(BaseModel):
    id: str
    object: str = "finetune_job"
    dataset_file_id: str
    base_model_id: str
    lora_rank: int
    lora_alpha: int
    lora_dropout: float
    target_modules: Optional[str] = None
    epochs: int
    learning_rate: float
    micro_batch_size: int
    max_seq_length: int
    status: str
    error_details: Optional[str] = None
    output_adapter_file_id: Optional[str] = None
    progress: FinetuneProgress = FinetuneProgress()
    created_at: int
    requested_at: Optional[int] = None
    started_at: Optional[int] = None
    completed_at: Optional[int] = None

    @classmethod
    def from_job(cls, job):
        return cls(
            id=job.id,
            dataset_file_id=job.dataset_file_id,
            base_model_id=job.base_model_id,
            lora_rank=job.lora_rank,
            lora_alpha=job.lora_alpha,
            lora_dropout=job.lora_dropout,
            target_modules=job.target_modules,
            epochs=job.epochs,
            learning_rate=job.learning_rate,
            micro_batch_size=job.micro_batch_size,
            max_seq_length=job.max_seq_length,
            status=job.status,
            error_details=job.error_details,
            output_adapter_file_id=job.output_adapter_file_id,
            progress=FinetuneProgress(
                current_step=job.current_step,
                total_steps=job.total_steps,
                current_epoch=job.current_epoch,
                latest_loss=job.latest_loss,
                checkpoint_step=job.checkpoint_step,
            ),
            created_at=job.created_at,
            requested_at=job.requested_at,
            started_at=job.started_at,
            completed_at=job.completed_at,
        )


class FinetuneProgressReport(BaseModel):
    """Live training progress from a worker (sent every N steps)."""
    job_id: str
    worker_id: str
    current_step: int
    total_steps: int
    current_epoch: float
    loss: float
    checkpoint_step: Optional[int] = None


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



