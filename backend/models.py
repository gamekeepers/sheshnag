from database import Base
from sqlalchemy import Column, String, Integer, Boolean, Float, Text, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
import uuid
import hashlib


def generate_user_id():
    return f"user-{uuid.uuid4().hex[:24]}"


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


def unix_now():
    return int(datetime.now(timezone.utc).timestamp())


# ─── Core Identity ──────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id                   = Column(String, primary_key=True, default=generate_user_id)
    email                = Column(String, unique=True, nullable=False)
    password_hash        = Column(String, nullable=False)
    full_name            = Column(String, nullable=False)
    platform_role        = Column(String, default="user")  # "user" or "superadmin"
    is_active            = Column(Boolean, default=True)
    must_change_password = Column(Boolean, default=False)
    created_at           = Column(Integer, default=unix_now)

    memberships = relationship("OrganizationMembership", back_populates="user")


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
    gpus           = Column(Text, default="[]")
    runtimes       = Column(Text, default="[]")
    # Liveness (spec §8.1): online | offline | draining | error.
    # Managed server-side (heartbeat arrival / sweeper timeout).
    status         = Column(String, default="online")
    # What the daemon is doing right now: idle | busy | downloading_model.
    # Reported by the worker in each heartbeat, separate from liveness.
    activity       = Column(String, default="idle")
    # Dynamic capability data, updated on every heartbeat (spec §8.1).
    # NULL until the first heartbeat arrives — poll uses that to fall
    # back to registration-advertised models.
    vram_total_gb     = Column(Float, nullable=True)
    vram_available_gb = Column(Float, nullable=True)
    loaded_models     = Column(Text, nullable=True)  # JSON array
    last_heartbeat = Column(Integer, default=unix_now)
    created_at     = Column(Integer, default=unix_now)

    organization = relationship("Organization", back_populates="workers")


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


# ─── Password Reset ────────────────────────────────────────

class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id         = Column(String, primary_key=True)
    user_id    = Column(String, nullable=False)
    token      = Column(String, unique=True, nullable=False)
    expires_at = Column(Integer, nullable=False)
    used       = Column(Boolean, default=False)
    created_at = Column(Integer, default=unix_now)
