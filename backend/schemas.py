from pydantic import AfterValidator, ConfigDict, Field, BaseModel
from typing import Annotated, Literal, Optional, List, Union

from finetune_contract import (
    ALL_LINEAR, ALLOWED_LORA_RANKS, DEFAULT_HYPERPARAMETERS, DEFAULT_LORA,
    DEFAULT_SEED, SERVABLE_TARGET_MODULES,
)


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
#
# Request models forbid unknown keys. G2 found Axolotl silently dropping a
# typo'd `lora_rnk` and training with the wrong rank — the API must not
# repeat that one layer up. Limits come from finetune_contract.py.

def _check_rank(v: int) -> int:
    if v not in ALLOWED_LORA_RANKS:
        raise ValueError(
            f"rank must be one of {list(ALLOWED_LORA_RANKS)} — serving starts vLLM with "
            f"--max-lora-rank {max(ALLOWED_LORA_RANKS)}, and a larger adapter stops the server from starting"
        )
    return v


def _check_target_modules(v):
    if v == ALL_LINEAR:
        return v
    if isinstance(v, list) and v:
        unknown = sorted(set(v) - SERVABLE_TARGET_MODULES)
        if unknown:
            raise ValueError(
                f"target_modules {unknown} are not servable; use \"{ALL_LINEAR}\" or a subset of "
                f"{sorted(SERVABLE_TARGET_MODULES)}"
            )
        return sorted(set(v))
    raise ValueError(f"target_modules must be \"{ALL_LINEAR}\" or a non-empty list of module names")


class FinetuneHyperparameters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    n_epochs: int = Field(DEFAULT_HYPERPARAMETERS["n_epochs"], ge=1, le=20)
    batch_size: int = Field(DEFAULT_HYPERPARAMETERS["batch_size"], ge=1, le=256)
    learning_rate: float = Field(DEFAULT_HYPERPARAMETERS["learning_rate"], gt=0, le=1e-2)
    max_seq_length: int = Field(DEFAULT_HYPERPARAMETERS["max_seq_length"], ge=128, le=8192)


class FinetuneLora(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rank: Annotated[int, AfterValidator(_check_rank)] = DEFAULT_LORA["rank"]
    alpha: int = Field(DEFAULT_LORA["alpha"], ge=1, le=512)
    dropout: float = Field(DEFAULT_LORA["dropout"], ge=0, lt=1)
    target_modules: Annotated[Union[str, List[str]], AfterValidator(_check_target_modules)] = DEFAULT_LORA["target_modules"]


class FinetuneMethod(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["supervised"] = "supervised"


class FinetuneJobCreate(BaseModel):
    """POST /v1/fine_tuning/jobs — OpenAI's request shape, plus `lora`."""
    model_config = ConfigDict(extra="forbid")

    model: str                              # catalogue id of an HF-sourced base model
    training_file: str                      # File.id from POST /v1/files
    validation_file: Optional[str] = None
    suffix: Optional[str] = Field(None, pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")
    seed: int = DEFAULT_SEED
    method: FinetuneMethod = FinetuneMethod()
    hyperparameters: FinetuneHyperparameters = FinetuneHyperparameters()
    lora: FinetuneLora = FinetuneLora()


class FinetuneProgressReport(BaseModel):
    """Live training progress from a worker — one per trainer log step.
    Becomes one `metrics` event. Field names follow LLaMA-Factory's
    trainer_log.jsonl (current_steps/total_steps/loss/lr/epoch)."""
    job_id: str
    worker_id: str
    current_step: int
    total_steps: int
    current_epoch: float
    loss: Optional[float] = None
    learning_rate: Optional[float] = None
    eval_loss: Optional[float] = None
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



