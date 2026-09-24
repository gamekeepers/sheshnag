from pydantic import AfterValidator, Field, BaseModel, field_validator
from typing import Annotated, Dict, Literal, Optional, List


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


class UsageStats(BaseModel):
    """Additive extension to OpenAI Batch object for token accounting (spec §16)."""
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None


class UsageRecordOut(BaseModel):
    """Per-prompt usage record for GET /v1/batches/{id}/usage."""
    id: str
    batch_id: str
    custom_id: str
    model: Optional[str] = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    created_at: int

    class Config:
        from_attributes = True


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
    # When a worker actually picked the job up. The Batch row has no column for
    # it — the moment is recorded on BatchAssignment.assigned_at — so callers
    # that want the full lifecycle pass it in. None means "not dispatched yet",
    # which is the difference between a batch that is queued and one that is
    # running, and the two look identical without it.
    in_progress_at: Optional[int] = None
    # Id of the worker that took the job, from the assignment row, so it still
    # resolves after the worker is gone. An opaque id rather than the hostname:
    # the consumer learns which machine answered, not whose. None until dispatched.
    worker_id: Optional[str] = None
    completed_at: Optional[int] = None
    request_counts: RequestCounts = RequestCounts()
    usage: Optional[UsageStats] = None
    metadata: Optional[Dict[str, str]] = None

    @classmethod
    def from_batch(
        cls,
        batch,
        in_progress_at: Optional[int] = None,
        worker_id: Optional[str] = None,
    ):
        has_usage = (
            getattr(batch, "prompt_tokens", None) is not None
            or getattr(batch, "completion_tokens", None) is not None
            or getattr(batch, "total_tokens", None) is not None
        )
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
            in_progress_at=in_progress_at,
            worker_id=worker_id,
            completed_at=batch.completed_at,
            metadata=getattr(batch, "batch_metadata", None),
            request_counts=RequestCounts(
                total=batch.request_counts_total or 0,
                completed=batch.request_counts_completed or 0,
                failed=batch.request_counts_failed or 0,
            ),
            usage=(
                UsageStats(
                    prompt_tokens=batch.prompt_tokens,
                    completion_tokens=batch.completion_tokens,
                    total_tokens=batch.total_tokens,
                )
                if has_usage
                else None
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
    usage: Optional[UsageStats] = None

    @classmethod
    def from_batch(cls, batch):
        has_usage = (
            getattr(batch, "prompt_tokens", None) is not None
            or getattr(batch, "completion_tokens", None) is not None
            or getattr(batch, "total_tokens", None) is not None
        )
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
            usage=(
                UsageStats(
                    prompt_tokens=batch.prompt_tokens,
                    completion_tokens=batch.completion_tokens,
                    total_tokens=batch.total_tokens,
                )
                if has_usage
                else None
            ),
        )


class BatchCreate(BaseModel):
    input_file_id: str
    endpoint: str
    completion_window: str = "24h"
    # OpenAI's shape: up to 16 string→string pairs, values ≤ 512 chars.
    metadata: Optional[Dict[str, str]] = None

    @field_validator("metadata")
    @classmethod
    def _bounded_metadata(cls, v):
        if v is None:
            return v
        if len(v) > 16:
            raise ValueError("metadata may hold at most 16 keys")
        for key, value in v.items():
            if len(key) > 64 or len(value) > 512:
                raise ValueError("metadata keys are ≤ 64 chars and values ≤ 512 chars")
        return v


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


class InventoryFile(BaseModel):
    """One file of a multi-file artifact (safetensors shard) with its hash."""
    file: str
    sha256: Optional[str] = None
    size_bytes: Optional[int] = None


class InventoryItem(BaseModel):
    """One on-disk artifact a worker's runtime holds (#116 identity join).

    `sha256` is the artifact FILE's hash (Ollama manifest layer digest,
    which equals the GGUF file sha256) — never a runtime manifest digest.
    None when the runtime can't report it (vLLM, unreadable models dir);
    matching then falls back to the name.
    """
    local_name: str
    sha256: Optional[str] = None
    # Not persisted yet (runtime_models has no size column); carried for the
    # reconciliation loop / provider dashboard so the wire shape is stable.
    size_bytes: Optional[int] = None
    loaded: bool = False
    runtime: Optional[str] = None
    # Descriptive facts the runtime knows about the artifact (quantization,
    # parameter_size, context_length, family) — what auto-adopt needs to
    # register a discovered model without a human. Null for vLLM.
    details: Optional[dict] = None
    # Every weight file of a multi-file artifact (vLLM safetensors shards);
    # `sha256` above is shard 1. Pinned into catalog_artifact_files on adopt.
    files: Optional[List[InventoryFile]] = None


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
    # None = unknown (unified memory has no machine-wide "in use" counter).
    vram_available_gb: Optional[float] = None
    # Free system RAM. None = unknown (older daemon, or a platform with no
    # reading) — never coerce to 0, which would read as "saturated".
    ram_available_gb: Optional[float] = None
    loaded_models: List[str] = []
    # Legacy name → /api/tags MANIFEST digest map. Accepted for wire
    # compatibility, ignored by the backend (not an artifact identity);
    # file hashes travel in `inventory`.
    loaded_model_digests: dict = {}
    # Full on-disk inventory, resent whole every beat (additive). Richer
    # than loaded_models: covers unloaded artifacts and carries file
    # hashes, so availability rows stay identity-true and drift (a manual
    # `ollama pull`) surfaces on the next beat.
    inventory: List[InventoryItem] = []
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
    cuda: Optional[str] = None      # NVIDIA only
    rocm: Optional[str] = None      # AMD only (spec §8.4 worker_gpus.rocm)


class RuntimeInfo(BaseModel):
    type: str                   # "ollama", "vllm", etc.
    endpoint: str
    # What the daemon verified at startup, not what it was configured to run.
    # Older daemons omit it and default to "ready", which is what they meant:
    # they only ever registered runtimes they had reached.
    status: str = "ready"
    models: List[str] = []
    # Legacy name → /api/tags MANIFEST digest map. Accepted for wire
    # compatibility, ignored by the backend: it is not an artifact identity
    # (see InventoryItem.sha256). Remove once all daemons send `inventory`.
    model_digests: dict = {}
    # Full on-disk inventory with file hashes (additive; older daemons
    # omit it and their rows keep a null digest -> name matching).
    inventory: List[InventoryItem] = []
    # What the runtime can honour beyond plain chat, and its server version.
    # Older daemons omit both; the backend then routes as if it claims nothing.
    capabilities: Optional[Dict[str, bool]] = None
    version: Optional[str] = None


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



