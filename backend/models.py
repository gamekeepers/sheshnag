from database import Base
from sqlalchemy import (
    BigInteger, Column, String, Integer, Boolean, Float, Text, ForeignKey,
    UniqueConstraint, JSON,
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


def generate_profile_id():
    return f"sprof-{uuid.uuid4().hex[:24]}"


def generate_artifact_file_id():
    return f"caf-{uuid.uuid4().hex[:24]}"


def generate_usage_id():
    return f"usage-{uuid.uuid4().hex[:24]}"


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
        # `runtimes[0]` is the fallback target for a heartbeat item that carries
        # no runtime tag, so it has to name the runtime the daemon lists first —
        # the one an untagged item most likely came from. Neither uuid4 ids nor a
        # second-granularity created_at shared by every row of one registration
        # call can express that, hence `position`.
        order_by="WorkerRuntime.position",
    )

    def loaded_model_names(self) -> list:
        """Model names currently loaded in VRAM (from heartbeats)."""
        return [m.name for rt in self.runtimes for m in rt.models if m.loaded]

    def advertised_model_names(self) -> set:
        """All model names this worker's runtimes host."""
        return {m.name for rt in self.runtimes for m in rt.models}

    def advertised_models(self) -> list:
        """(name, digest) pairs this worker's runtimes host AND may be
        scheduled: quarantined (unregistered), drifted, and missing rows are
        excluded, as is every model of a runtime that is not itself
        schedulable, and every unloaded model of a runtime that serves only
        what it has loaded — so every picker/capacity path shares the rule.

        `advertised_model_names` deliberately does not filter — cataloguing
        what a worker holds is a different question from what it can be given."""
        return [
            (m.name, m.digest)
            for rt in self.runtimes if rt.schedulable
            for m in rt.models
            if m.schedulable and (m.loaded or not rt.serves_only_loaded)
        ]

    def loaded_models(self) -> list:
        """(name, digest) pairs currently loaded in VRAM (schedulable only)."""
        return [
            (m.name, m.digest)
            for rt in self.runtimes if rt.schedulable
            for m in rt.models if m.loaded and m.schedulable
        ]


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

    # A runtime that is draining or was never reached still lists its models —
    # they are on that worker's disk and that is worth cataloguing — but it
    # cannot be given work. Mirrors RuntimeModel.schedulable so every picker
    # and capacity path applies one rule.
    # NULL for rows written before the column existed, and for daemons that
    # do not send it. Both meant "everything advertised is servable", which is
    # what True says — so only an explicit False narrows dispatch.
    loads_on_demand = Column(Boolean, nullable=True, default=True)

    SCHEDULABLE_STATUSES = frozenset({"ready"})

    @property
    def schedulable(self) -> bool:
        return self.status in self.SCHEDULABLE_STATUSES

    @property
    def serves_only_loaded(self) -> bool:
        """A vLLM instance serves the one model it was started with; the rest
        of its hub cache is catalogue, not capacity."""
        return self.loads_on_demand is False
    # Index into the daemon's configured runtime list, assigned at registration.
    # Rows predating the column are NULL and tie, which costs nothing: a worker
    # registered before it existed has one runtime, and replace-all registration
    # rewrites every row with a position on the daemon's next start.
    position   = Column(Integer, nullable=False, default=0)
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
    digest           = Column(String, nullable=True)  # artifact FILE sha256 (identity join key)
    # Catalogue entry this row's hash was verified against (#116 reconcile).
    # NULL = not hash-verified: either an unverifiable row (no hash reported)
    # that name-matches, or a row in one of the NON_SCHEDULABLE states.
    catalog_id = Column(
        String, ForeignKey("model_catalog.id", ondelete="SET NULL"), nullable=True,
    )
    # available | downloading | not_downloaded | error — plus the reconcile
    # states: unregistered (hash matches no entry; quarantined), drift (name
    # claims a pinned entry, bytes differ), missing (dropped from a full
    # inventory — e.g. `ollama rm` on the box). The picker never routes to
    # NON_SCHEDULABLE rows; see reconciliation.py.
    status     = Column(String, default="available")
    loaded     = Column(Boolean, default=False)
    # Runtime-reported facts (quantization, parameter_size, context_length,
    # family) — pre-fills adopt / auto-adopt; descriptive only.
    details    = Column(JSON, nullable=True)
    size_bytes = Column(BigInteger, nullable=True)   # artifact size as reported; feeds vram estimates
    files      = Column(JSON, nullable=True)         # [{file, sha256, size_bytes}] for multi-shard artifacts
    last_used_at = Column(Integer, nullable=True)
    created_at = Column(Integer, default=unix_now)
    updated_at = Column(Integer, default=unix_now)

    runtime = relationship("WorkerRuntime", back_populates="models")
    catalog_entry = relationship("ModelCatalog", foreign_keys=[catalog_id])

    NON_SCHEDULABLE_STATUSES = frozenset({"unregistered", "drift", "missing"})

    @property
    def schedulable(self) -> bool:
        return self.status not in self.NON_SCHEDULABLE_STATUSES


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
    # DEPRECATED pair: runtime coupling now lives on `serving_profiles`
    # (one entry, several runtimes). Kept dual-written by the seed so
    # existing readers (dispatch, validator) keep working until they are
    # migrated to `serving_targets()`; do not add new readers.
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
    # What the MODEL can do (properties of the weights: vision, embeddings,
    # json_mode, logprobs). Runtime mechanism differences (grammar vs guided
    # decoding) are the executor's capabilities(), not stored here; effective
    # capability = model AND runtime.
    capabilities     = Column(JSON, nullable=True)
    # Upstream base weights (HF repo id) — groups quants/formats of the same
    # model for the dashboard and duplicate checks. Organizational only,
    # never an identity or matching key; NULL = ungrouped (unknown upstream).
    lineage          = Column(String, nullable=True)
    # Provenance (where the artifact came from / how to fetch it) — distinct
    # from identity (`digest`). source_ref + source_revision is the pull
    # reference (HF repo+commit, or Ollama library path); homepage_url is the
    # human-facing model card. Metadata only, never a matching key.
    source_type      = Column(String, nullable=True)    # 'ollama-library' | 'huggingface'
    source_ref       = Column(String, nullable=True)    # HF repo id / ollama library path
    source_revision  = Column(String, nullable=True)    # HF commit/tag; NULL for ollama
    homepage_url     = Column(String, nullable=True)    # model-card link for the dashboard
    org_id           = Column(String, ForeignKey("organizations.id"), nullable=True)  # NULL = public
    # active | requested | deprecated | unverified. `unverified` = adopted
    # from a worker's quarantined hash: selectable and schedulable like
    # active, but provenance (upstream/lineage) is unconfirmed.
    status           = Column(String, default="active")
    enabled          = Column(Boolean, default=True)
    created_at       = Column(Integer, default=unix_now)

    # How the entry came to exist: NULL = seeded from the manifest,
    # 'auto' = auto-adopt pass (registry-confirmed worker hash), else the
    # id of the admin who adopted it. Lets the Models tab list auto entries
    # for review without overloading `source_type` (which is provenance).
    adopted_by       = Column(String, nullable=True)

    SELECTABLE_STATUSES = ("active", "unverified")

    profiles = relationship(
        "ServingProfile", back_populates="entry",
        cascade="all, delete-orphan",
    )
    artifact_files = relationship(
        "CatalogArtifactFile", back_populates="entry",
        cascade="all, delete-orphan",
    )

    def serving_targets(self) -> list:
        """(runtime, runtime_model_id) pairs this entry can be served as.

        Serving profiles are the source of truth; entries whose profiles have
        not been seeded yet fall back to the legacy columns, so mixed states
        keep scheduling. A profile's `runtime_model_ids` are extra names the
        same artifact answers to (e.g. vLLM --served-model-name aliases on
        different boxes) — each expands to its own target.
        """
        if self.profiles:
            targets = []
            for p in self.profiles:
                targets.append((p.runtime, p.runtime_model_id))
                targets.extend((p.runtime, extra) for extra in (p.runtime_model_ids or []))
            return targets
        return [(self.runtime, self.runtime_model_id)]


class ServingProfile(Base):
    """How one catalogue artifact is served by one runtime.

    The registry entry (`ModelCatalog`) pins WHAT the artifact is; a profile
    binds it to a runtime plus that runtime's launch knobs (`params`, e.g.
    llama.cpp `n_ctx`/`parallel`, vLLM `max_model_len`/`gpu_mem_util`).
    Launch knobs are per-deployment and platform-owned — per-request
    sampling params still travel in each batch row's `body`, untouched.
    One entry may carry several profiles; adding a runtime is a new row
    here, never a registry change.
    """
    __tablename__ = "serving_profiles"
    __table_args__ = (
        UniqueConstraint("catalog_id", "runtime"),
    )

    id         = Column(String, primary_key=True, default=generate_profile_id)
    catalog_id = Column(
        String, ForeignKey("model_catalog.id", ondelete="CASCADE"), nullable=False,
    )
    runtime          = Column(String, nullable=False)  # ollama | vllm | llamacpp
    runtime_model_id = Column(String, nullable=False)  # exact id this runtime expects
    runtime_model_ids = Column(JSON, nullable=True)    # extra names the artifact answers to (vLLM aliases)
    params           = Column(JSON, nullable=True)     # server-launch knobs
    created_at = Column(Integer, default=unix_now)
    updated_at = Column(Integer, default=unix_now)

    entry = relationship("ModelCatalog", back_populates="profiles")


class CatalogArtifactFile(Base):
    """One file of a catalogue artifact, with its own hash.

    `ModelCatalog.digest` pins only the weights file; a vision GGUF is two
    files (weights + mmproj projector) and a safetensors model is many
    shards. Provisioning downloads and verifies every row here before an
    assignment counts as present — "artifact present" means complete, not
    just weights. Single-file entries may skip this table (digest suffices).
    """
    __tablename__ = "catalog_artifact_files"
    __table_args__ = (
        UniqueConstraint("catalog_id", "file"),
    )

    id         = Column(String, primary_key=True, default=generate_artifact_file_id)
    catalog_id = Column(
        String, ForeignKey("model_catalog.id", ondelete="CASCADE"), nullable=False,
    )
    file       = Column(String, nullable=False)      # filename within the source repo
    role       = Column(String, nullable=False, default="weights")  # weights | mmproj | shard
    sha256     = Column(String, nullable=True)
    size_bytes = Column(BigInteger, nullable=True)
    created_at = Column(Integer, default=unix_now)

    entry = relationship("ModelCatalog", back_populates="artifact_files")


# ─── Files & Batches ────────────────────────────────────────

class File(Base):
    __tablename__ = "files"

    id         = Column(String, primary_key=True, default=generate_file_id)
    user_id    = Column(String, nullable=True)
    filename   = Column(String, nullable=False)
    purpose    = Column(String, default="batch")
    # BigInteger, not Integer: Postgres INTEGER caps at 2^31-1, and a batch
    # input/output JSONL above 2 GiB would abort the upload with
    # "integer out of range" after the bytes had already hit disk.
    bytes      = Column(BigInteger, default=0)
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
    prompt_tokens            = Column(Integer, nullable=True)
    completion_tokens        = Column(Integer, nullable=True)
    total_tokens             = Column(Integer, nullable=True)

    usage_records = relationship(
        "UsageRecord", back_populates="batch",
        cascade="all, delete-orphan",
    )


class UsageRecord(Base):
    """Per-prompt token usage record for batch auditing and pricing (spec §16).

    Unique on (batch_id, custom_id) to guarantee idempotency across
    worker retries and requeued batches.
    """
    __tablename__ = "usage_records"
    __table_args__ = (
        UniqueConstraint("batch_id", "custom_id"),
    )

    id                = Column(String, primary_key=True, default=generate_usage_id)
    batch_id          = Column(
        String, ForeignKey("batches.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    custom_id         = Column(String, nullable=False)
    model             = Column(String, nullable=True)
    prompt_tokens     = Column(Integer, nullable=False, default=0)
    completion_tokens = Column(Integer, nullable=False, default=0)
    total_tokens      = Column(Integer, nullable=False, default=0)
    created_at        = Column(Integer, default=unix_now)

    batch = relationship("Batch", back_populates="usage_records")


class BatchAssignment(Base):
    __tablename__ = "batch_assignments"

    batch_id    = Column(String, primary_key=True)
    worker_id       = Column(String, nullable=False)
    # Snapshotted at assignment time so those views still resolve once the
    # worker is gone. The live Worker row, when it still exists, remains the
    # source of truth for the current hostname.
    org_id          = Column(String, nullable=False)
    worker_hostname = Column(String, nullable=True)
    assigned_at     = Column(Integer, default=unix_now)


# ─── Password Reset ────────────────────────────────────────

class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id         = Column(String, primary_key=True)
    user_id    = Column(String, nullable=False)
    token      = Column(String, unique=True, nullable=False)
    expires_at = Column(Integer, nullable=False)
    used       = Column(Boolean, default=False)
    created_at = Column(Integer, default=unix_now)
