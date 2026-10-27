from database import Base
from sqlalchemy import (
    Column, String, Integer, Boolean, Float, Text, ForeignKey, UniqueConstraint,
)
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
import uuid
import hashlib


def generate_user_id():
    return f"user-{uuid.uuid4().hex[:24]}"


def generate_allowed_domain_id():
    return f"domain-{uuid.uuid4().hex[:24]}"


def generate_org_id():
    return f"org-{uuid.uuid4().hex[:24]}"


def generate_worker_id():
    return f"worker-{uuid.uuid4().hex[:24]}"


def generate_membership_id():
    return f"mem-{uuid.uuid4().hex[:24]}"


def generate_api_key_id():
    return f"key-{uuid.uuid4().hex[:24]}"


def generate_file_id():
    return f"file-{uuid.uuid4().hex[:24]}"


def generate_batch_id():
    return f"batch-{uuid.uuid4().hex[:24]}"


def generate_runtime_id():
    return f"wrt-{uuid.uuid4().hex[:24]}"


def generate_runtime_model_id():
    return f"rtm-{uuid.uuid4().hex[:24]}"


def generate_gpu_id():
    return f"gpu-{uuid.uuid4().hex[:24]}"


def generate_catalog_id():
    return f"mdl-{uuid.uuid4().hex[:24]}"


def generate_finetune_job_id():
    return f"ftjob-{uuid.uuid4().hex[:24]}"


def generate_finetune_artifact_id():
    return f"ftart-{uuid.uuid4().hex[:24]}"


def unix_now():
    return int(datetime.now(timezone.utc).timestamp())


# ─── Core Identity ──────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id                   = Column(String, primary_key=True, default=generate_user_id)
    email                = Column(String, unique=True, nullable=False)
    password_hash        = Column(String, nullable=True)   # NULL for Google-only users
    full_name            = Column(String, nullable=False)
    platform_role        = Column(String, default="user")  # "user" or "superadmin"
    is_active            = Column(Boolean, default=True)
    must_change_password = Column(Boolean, default=False)
    google_id            = Column(String, unique=True, nullable=True)   # Google sub (unique user ID)
    auth_provider        = Column(String, default="local")              # "local" | "google" | "both"
    created_at           = Column(Integer, default=unix_now)

    memberships = relationship("OrganizationMembership", back_populates="user")


class AllowedEmailDomain(Base):
    """A domain permitted to self-register.

    Governs *self-service* signup only — both the password and Google paths.
    Invites and superadmin-created users are deliberately exempt: inviting an
    external collaborator is a considered act, and gating it would break
    cross-institution work.

    **An empty table means no restriction.** Fail-open is deliberate: a fresh
    install has no superadmin yet, so failing closed would lock out the very
    account needed to add the first domain. Enforcement is opt-in, switched on
    by adding one row.
    """
    __tablename__ = "allowed_email_domains"

    id                 = Column(String, primary_key=True, default=generate_allowed_domain_id)
    domain             = Column(String, unique=True, nullable=False)  # lower-case, no leading '@'
    include_subdomains = Column(Boolean, default=False)
    note               = Column(String, nullable=True)   # e.g. "Students"
    created_by_id      = Column(String, ForeignKey("users.id"), nullable=True)
    created_at         = Column(Integer, default=unix_now)


class Organization(Base):
    __tablename__ = "organizations"

    id         = Column(String, primary_key=True, default=generate_org_id)
    name       = Column(String, nullable=False)
    created_at = Column(Integer, default=unix_now)

    memberships = relationship("OrganizationMembership", back_populates="org")
    workers     = relationship("Worker", back_populates="organization")
    api_keys    = relationship("ApiKey", back_populates="organization")


def get_org_owner(db, org_id: str):
    """Derive the owner user_id from memberships where role='owner'."""
    m = db.query(OrganizationMembership).filter(
        OrganizationMembership.org_id == org_id,
        OrganizationMembership.role == "owner",
    ).first()
    return m.user_id if m else None


def get_org_owner_for_org_obj(db, org):
    """Convenience: derive owner_id from an already-queried Organization instance."""
    return get_org_owner(db, org.id)


class OrganizationMembership(Base):
    __tablename__ = "organization_memberships"

    id         = Column(String, primary_key=True, default=generate_membership_id)
    org_id     = Column(String, ForeignKey("organizations.id"), nullable=False)
    user_id    = Column(String, ForeignKey("users.id"), nullable=False)
    role       = Column(String, nullable=False)   # "owner", "admin", "viewer"
    created_at = Column(Integer, default=unix_now)

    org  = relationship("Organization", back_populates="memberships")
    user = relationship("User", back_populates="memberships")


# ─── Organization Invites ───────────────────────────────────

def generate_invite_id():
    return f"inv-{uuid.uuid4().hex[:24]}"


class OrganizationInvite(Base):
    __tablename__ = "organization_invites"

    id              = Column(String, primary_key=True, default=generate_invite_id)
    org_id          = Column(String, ForeignKey("organizations.id"), nullable=False)
    inviter_id      = Column(String, ForeignKey("users.id"), nullable=False)
    invitee_email   = Column(String, nullable=False)
    invitee_user_id = Column(String, ForeignKey("users.id"), nullable=True)
    role            = Column(String, nullable=False)         # role offered on acceptance
    token           = Column(String, unique=True, nullable=False, index=True)
    expires_at      = Column(Integer, nullable=False)        # unix timestamp
    accepted        = Column(Boolean, default=False)
    created_at      = Column(Integer, default=unix_now)

    org     = relationship("Organization")
    inviter = relationship("User", foreign_keys=[inviter_id])


# ─── API Keys ───────────────────────────────────────────────

class ApiKey(Base):
    __tablename__ = "api_keys"

    id                  = Column(String, primary_key=True, default=generate_api_key_id)
    org_id              = Column(String, ForeignKey("organizations.id"), nullable=True)          # required for worker keys, NULL for personal
    key_type            = Column(String, nullable=False)         # "worker" or "personal"
    created_by_user_id  = Column(
        String, ForeignKey("users.id"), nullable=False,
    )
    # Note: back-reference exists via .creator below, but no reciprocal
    # User.api_keys — keys are sensitive and loaded on-demand only.
    name                = Column(String, nullable=False)
    key_prefix          = Column(String, nullable=False)   # first 8 chars shown in UI
    key_hash            = Column(String, unique=True, nullable=False)  # SHA-256 of full key
    status              = Column(String, nullable=False, default="active")
    last_used_at        = Column(Integer, nullable=True)
    expires_at          = Column(Integer, nullable=True)
    created_at          = Column(Integer, nullable=False, default=unix_now)
    revoked_at          = Column(Integer, nullable=True)

    organization = relationship("Organization", back_populates="api_keys")
    creator      = relationship(
        "User",
        foreign_keys=[created_by_user_id],
        lazy="select",
        doc="Back-reference to the user who created this key.",
    )


# ─── Workers ────────────────────────────────────────────────

class Worker(Base):
    __tablename__ = "workers"

    id             = Column(String, primary_key=True, default=generate_worker_id)
    org_id         = Column(String, ForeignKey("organizations.id"), nullable=False)
    api_key_id     = Column(String, nullable=True)
    hostname       = Column(String, nullable=False)
    os             = Column(String, nullable=True)
    cpu_cores      = Column(Integer, nullable=True)
    ram_total_gb   = Column(Float, nullable=True)
    # Liveness (spec §8.1): online | offline | draining | error.
    # Managed server-side (heartbeat arrival / sweeper timeout).
    status         = Column(String, default="online")
    # What the daemon is doing right now: idle | busy | downloading_model.
    # Reported by the worker in each heartbeat, separate from liveness.
    activity       = Column(String, default="idle")
    # Aggregate VRAM, updated on every heartbeat (spec §8.1). NULL until
    # the first heartbeat arrives — poll uses that to fall back to
    # registration-advertised models. Kept machine-aggregate because the
    # daemon reports totals, not per-GPU stats.
    vram_total_gb     = Column(Float, nullable=True)
    vram_available_gb = Column(Float, nullable=True)
    last_heartbeat = Column(Integer, default=unix_now)
    created_at     = Column(Integer, default=unix_now)

    organization = relationship("Organization", back_populates="workers")
    # Normalized inventory (spec §8.2–8.4). Assigning a new list replaces
    # the old rows (delete-orphan), which is how re-registration works.
    gpus     = relationship(
        "WorkerGpu", back_populates="worker",
        cascade="all, delete-orphan", order_by="WorkerGpu.gpu_index",
    )
    runtimes = relationship(
        "WorkerRuntime", back_populates="worker",
        cascade="all, delete-orphan",
    )

    def loaded_model_names(self) -> list:
        """Model names currently loaded in VRAM (from heartbeats)."""
        return [m.name for rt in self.runtimes for m in rt.models if m.loaded]

    def advertised_model_names(self) -> set:
        """All model names this worker's runtimes host."""
        return {m.name for rt in self.runtimes for m in rt.models}

    def advertised_models(self) -> list:
        """(name, digest) pairs this worker's runtimes host."""
        return [(m.name, m.digest) for rt in self.runtimes for m in rt.models]

    def loaded_models(self) -> list:
        """(name, digest) pairs currently loaded in VRAM."""
        return [(m.name, m.digest) for rt in self.runtimes for m in rt.models if m.loaded]


class WorkerRuntime(Base):
    """An inference engine a worker exposes (spec §8.2)."""
    __tablename__ = "worker_runtimes"
    __table_args__ = (
        UniqueConstraint("worker_id", "engine", "base_url"),
    )

    id         = Column(String, primary_key=True, default=generate_runtime_id)
    worker_id  = Column(
        String, ForeignKey("workers.id", ondelete="CASCADE"), nullable=False,
    )
    engine     = Column(String, nullable=False)   # ollama | vllm | tgi | transformers
    base_url   = Column(String, nullable=False, default="")
    api_protocol = Column(String, default="openai-compatible")
    status     = Column(String, default="ready")  # ready | draining | unavailable
    max_concurrent_requests = Column(Integer, nullable=True)
    request_timeout_seconds = Column(Integer, nullable=True)
    created_at = Column(Integer, default=unix_now)
    updated_at = Column(Integer, default=unix_now)

    worker = relationship("Worker", back_populates="runtimes")
    models = relationship(
        "RuntimeModel", back_populates="runtime",
        cascade="all, delete-orphan",
    )


class RuntimeModel(Base):
    """A model hosted by a worker runtime — the per-worker *availability*
    row. Lean by design: scheduling only needs name/digest match + loaded
    state. Descriptive/curated metadata (quantization, params, context,
    task type, size) lives on `ModelCatalog`, not replicated here.

    `loaded` (in VRAM right now) is transient heartbeat state; `status`
    tracks on-disk availability.
    """
    __tablename__ = "runtime_models"
    __table_args__ = (
        UniqueConstraint("runtime_id", "name"),
    )

    id         = Column(String, primary_key=True, default=generate_runtime_model_id)
    runtime_id = Column(
        String, ForeignKey("worker_runtimes.id", ondelete="CASCADE"), nullable=False,
    )
    name             = Column(String, nullable=False)
    runtime_model_id = Column(String, nullable=True)  # exact id the runtime expects
    digest           = Column(String, nullable=True)  # artifact digest (reproducibility join key)
    status     = Column(String, default="available")  # available | downloading | not_downloaded | error
    loaded     = Column(Boolean, default=False)
    last_used_at = Column(Integer, nullable=True)
    created_at = Column(Integer, default=unix_now)
    updated_at = Column(Integer, default=unix_now)

    runtime = relationship("WorkerRuntime", back_populates="models")


class WorkerGpu(Base):
    """A physical GPU on a worker (spec §8.4)."""
    __tablename__ = "worker_gpus"
    __table_args__ = (
        UniqueConstraint("worker_id", "gpu_index"),
    )

    id        = Column(String, primary_key=True, default=generate_gpu_id)
    worker_id = Column(
        String, ForeignKey("workers.id", ondelete="CASCADE"), nullable=False,
    )
    gpu_index = Column(Integer, nullable=False)
    vendor    = Column(String, nullable=True)
    name      = Column(String, nullable=True)
    vram_gb   = Column(Float, nullable=True)
    driver    = Column(String, nullable=True)
    cuda      = Column(String, nullable=True)
    rocm      = Column(String, nullable=True)
    updated_at = Column(Integer, default=unix_now)

    worker = relationship("Worker", back_populates="gpus")


# ─── Model Catalogue ────────────────────────────────────────

class ModelCatalog(Base):
    """A curated, pinned model the user may select for a batch.

    Identity is curated (admin/seed), not derived from what workers
    registered: `id` is a stable platform slug the user puts in
    `body.model`; `runtime_model_id` (the raw `mistral:7b` / HF repo id)
    is an internal detail. Each row is ONE concrete artifact — weights +
    quantization + runtime — so a batch bound to it never silently swaps
    precision or runtime (reproducibility).

    `digest` is the reproducibility anchor and the intended join key
    against a worker's advertised models; until the daemon reports
    digests, availability is matched on `runtime_model_id` (see
    provider_picker). `org_id` NULL = public; reserved for org-private
    entries (tier 2, not wired yet).
    """
    __tablename__ = "model_catalog"

    id               = Column(String, primary_key=True, default=generate_catalog_id)
    display_name     = Column(String, nullable=False)
    runtime          = Column(String, nullable=False)   # ollama | vllm
    runtime_model_id = Column(String, nullable=False)   # exact runtime string, internal
    digest           = Column(String, nullable=True)    # reproducibility pin / join key (identity)
    quantization     = Column(String, nullable=True)
    vram_gb          = Column(Float, nullable=True)     # scheduling requirement
    size_gb          = Column(Float, nullable=True)     # disk (feeds download size cap)
    # Descriptive metadata (curated, one place) — moved off the per-worker
    # runtime_models rows. For the Models tab / picker, not scheduling.
    task_type        = Column(String, nullable=True)    # chat | text-generation | embedding | vision
    parameter_size   = Column(String, nullable=True)    # human-readable, e.g. '7B'
    context_length   = Column(Integer, nullable=True)
    # Provenance (where the artifact came from / how to fetch it) — distinct
    # from identity (`digest`). source_ref + source_revision is the pull
    # reference (HF repo+commit, or Ollama library path); homepage_url is the
    # human-facing model card. Metadata only, never a matching key.
    source_type      = Column(String, nullable=True)    # 'ollama-library' | 'huggingface'
    source_ref       = Column(String, nullable=True)    # HF repo id / ollama library path
    source_revision  = Column(String, nullable=True)    # HF commit/tag; NULL for ollama
    homepage_url     = Column(String, nullable=True)    # model-card link for the dashboard
    org_id           = Column(String, ForeignKey("organizations.id"), nullable=True)  # NULL = public
    status           = Column(String, default="active")  # active | requested | deprecated
    enabled          = Column(Boolean, default=True)
    created_at       = Column(Integer, default=unix_now)


# ─── Files & Batches ────────────────────────────────────────

class File(Base):
    __tablename__ = "files"

    id         = Column(String, primary_key=True, default=generate_file_id)
    user_id    = Column(String, nullable=True)
    filename   = Column(String, nullable=False)
    purpose    = Column(String, default="batch")
    bytes      = Column(Integer, default=0)
    filepath   = Column(String, nullable=False, default="")
    created_at = Column(Integer, default=unix_now)


class Batch(Base):
    __tablename__ = "batches"

    id                       = Column(String, primary_key=True, default=generate_batch_id)
    user_id                  = Column(String, nullable=True)
    api_key_id               = Column(String, nullable=True)  # attribution for personal key usage
    endpoint                 = Column(String, nullable=False)
    model                    = Column(String, nullable=True)
    input_file_id            = Column(String, nullable=False)
    completion_window        = Column(String, default="24h")
    status                   = Column(String, default="validating")
    output_file_id           = Column(String, nullable=True)
    error_file_id            = Column(String, nullable=True)
    created_at               = Column(Integer, default=unix_now)
    expires_at               = Column(Integer, nullable=True)
    requested_at             = Column(Integer, nullable=True)
    completed_at             = Column(Integer, nullable=True)
    request_counts_total     = Column(Integer, default=0)
    request_counts_completed = Column(Integer, default=0)
    request_counts_failed    = Column(Integer, default=0)
    error_details            = Column(String, nullable=True)
    attempts                 = Column(Integer, default=0)  # execution attempts (spec §12 requeue)


class BatchAssignment(Base):
    __tablename__ = "batch_assignments"

    batch_id    = Column(String, primary_key=True)
    worker_id   = Column(String, ForeignKey("workers.id"), nullable=False)
    assigned_at = Column(Integer, default=unix_now)


# ─── Finetuning ─────────────────────────────────────────────
#
# Three new tables, never new columns on batches (create_all() can add a
# table to everyone's dev DB for free; it can never add a column). The
# contract they implement — statuses, limits, which fields serving needs —
# lives in finetune_contract.py and docs/finetune_contract.md.

class FinetuneJob(Base):
    """A LoRA finetuning job — the platform's own `/v1/fine_tuning/jobs`
    object, shaped after OpenAI's `fine_tuning.job`.

    Sibling to Batch, not a variant of it: hyperparameters in, an adapter
    artifact out, minutes-to-hours of runtime, and its own state machine
    (finetune_contract.VALID_TRANSITIONS).

    The base model is *pinned at creation*: the catalogue id plus the
    source ref and revision it resolved to at that moment. G1 showed vLLM
    never checks an adapter's base, so the job — not the catalogue's
    current state — is the record of what the adapter must be served on.
    """
    __tablename__ = "finetune_jobs"

    id                   = Column(String, primary_key=True, default=generate_finetune_job_id)
    user_id              = Column(String, nullable=False, index=True)
    api_key_id           = Column(String, nullable=True)  # attribution for personal key usage

    # Base model, pinned (see docstring).
    base_model_id        = Column(String, nullable=False)  # ModelCatalog.id (source_type='huggingface')
    base_source_ref      = Column(String, nullable=False)  # e.g. Qwen/Qwen2.5-0.5B-Instruct
    base_source_revision = Column(String, nullable=True)   # HF revision as catalogued (should be a commit sha)

    training_file_id     = Column(String, nullable=False)  # File.id, purpose="fine-tune"
    validation_file_id   = Column(String, nullable=True)

    method               = Column(String, nullable=False, default="supervised")
    suffix               = Column(String, nullable=True)   # user label folded into fine_tuned_model
    seed                 = Column(Integer, nullable=False, default=42)

    # Training hyperparameters.
    n_epochs             = Column(Integer, nullable=False)
    batch_size           = Column(Integer, nullable=False)  # effective batch
    learning_rate        = Column(Float, nullable=False)
    max_seq_length       = Column(Integer, nullable=False)

    # LoRA — the fields serving needs (G1). rank <= MAX_LORA_RANK is
    # enforced at the API; target_modules is "all-linear" or a JSON list.
    lora_rank            = Column(Integer, nullable=False)
    lora_alpha           = Column(Integer, nullable=False)
    lora_dropout         = Column(Float, nullable=False)
    target_modules       = Column(String, nullable=False)

    status               = Column(String, nullable=False, default="validating_files")
    error                = Column(Text, nullable=True)   # JSON {code, message, param, details?}
    attempts             = Column(Integer, nullable=False, default=0)

    # Validator output: example/token counts and the length histogram (JSON).
    training_file_stats  = Column(Text, nullable=True)
    trained_tokens       = Column(Integer, nullable=True)  # set on success

    # The adapter's serving name once succeeded — what a client passes as
    # `model` to /v1/chat/completions. Mirrors OpenAI's field of the same name.
    fine_tuned_model     = Column(String, nullable=True, unique=True)

    created_at           = Column(Integer, nullable=False, default=unix_now)
    queued_at            = Column(Integer, nullable=True)
    started_at           = Column(Integer, nullable=True)
    finished_at          = Column(Integer, nullable=True)


class FinetuneJobAssignment(Base):
    __tablename__ = "finetune_job_assignments"

    job_id      = Column(String, primary_key=True)
    worker_id   = Column(String, ForeignKey("workers.id"), nullable=False)
    assigned_at = Column(Integer, default=unix_now)


class FinetuneEvent(Base):
    """Append-only job log — status changes, validation results and training
    metrics. Mirrors OpenAI's `fine_tuning.job.event`.

    Progress lives here rather than in columns on the job: a loss curve is
    a series, and the runner's progress source (LLaMA-Factory's
    trainer_log.jsonl, one JSON object per log step) maps onto one
    `metrics` event per line. The integer id doubles as the pagination
    cursor (exposed as "ftevent-<id>").
    """
    __tablename__ = "finetune_events"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    job_id     = Column(String, ForeignKey("finetune_jobs.id"), nullable=False, index=True)
    created_at = Column(Integer, nullable=False, default=unix_now)
    level      = Column(String, nullable=False, default="info")     # info | warn | error
    type       = Column(String, nullable=False, default="message")  # message | metrics
    message    = Column(String, nullable=False)
    data       = Column(Text, nullable=True)  # JSON; for metrics: step, total_steps, epoch, loss, ...


class FinetuneArtifact(Base):
    """A file a job produced — the trained adapter (kind='adapter') or an
    intermediate checkpoint (kind='checkpoint', the future resume point).

    The field list is what G1 proved serving needs. Everything about the
    adapter is read by the backend from the uploaded zip's
    adapter_config.json, not taken on the worker's word; only the engine
    fields are self-reported.
    """
    __tablename__ = "finetune_artifacts"

    id                   = Column(String, primary_key=True, default=generate_finetune_artifact_id)
    job_id               = Column(String, ForeignKey("finetune_jobs.id"), nullable=False, index=True)
    kind                 = Column(String, nullable=False, default="adapter")  # adapter | checkpoint
    file_id              = Column(String, nullable=False)  # File.id of the zip
    step                 = Column(Integer, nullable=True)  # training step it was taken at
    sha256               = Column(String, nullable=False)  # of adapter_model.safetensors
    bytes                = Column(Integer, nullable=False)

    # Base identity — serving must load exactly this (vLLM won't check).
    base_model_id        = Column(String, nullable=False)
    base_source_ref      = Column(String, nullable=False)
    base_source_revision = Column(String, nullable=True)

    # From adapter_config.json.
    peft_type            = Column(String, nullable=True)   # must be LORA
    lora_rank            = Column(Integer, nullable=True)  # r; serving needs --max-lora-rank >= this
    lora_alpha           = Column(Integer, nullable=True)
    target_modules       = Column(String, nullable=True)   # JSON list, *resolved* (never "all-linear")
    modules_to_save      = Column(String, nullable=True)   # JSON list; non-null = unservable today

    # Engine that produced it (self-reported by the worker, pinned per G2).
    engine               = Column(String, nullable=True)
    engine_version       = Column(String, nullable=True)
    engine_image         = Column(String, nullable=True)

    metrics              = Column(Text, nullable=True)  # JSON, e.g. final train_loss / eval_loss

    # Verdict from finetune_contract.adapter_unservable_reasons().
    servable             = Column(Boolean, nullable=False, default=False)
    unservable_reasons   = Column(Text, nullable=True)  # JSON list

    # Set when the adapter is registered as a catalogue entry under its
    # base model (AC's catalogue rework). Reserved now so that step is a
    # write, not a migration.
    catalog_entry_id     = Column(String, nullable=True)

    created_at           = Column(Integer, nullable=False, default=unix_now)


# ─── Password Reset ────────────────────────────────────────

class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id         = Column(String, primary_key=True)
    user_id    = Column(String, nullable=False)
    token      = Column(String, unique=True, nullable=False)
    expires_at = Column(Integer, nullable=False)
    used       = Column(Boolean, default=False)
    created_at = Column(Integer, default=unix_now)
