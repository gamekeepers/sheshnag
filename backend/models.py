from database import Base
from sqlalchemy import Column, String, Integer, Boolean, Float, Text
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


class Organization(Base):
    __tablename__ = "organizations"

    id         = Column(String, primary_key=True, default=generate_org_id)
    name       = Column(String, nullable=False)
    owner_id   = Column(String, nullable=False)
    created_at = Column(Integer, default=unix_now)


class OrganizationMembership(Base):
    __tablename__ = "organization_memberships"

    id         = Column(String, primary_key=True, default=generate_membership_id)
    org_id     = Column(String, nullable=False)
    user_id    = Column(String, nullable=False)
    role       = Column(String, nullable=False)   # "owner", "admin", "viewer"
    created_at = Column(Integer, default=unix_now)


# ─── API Keys ───────────────────────────────────────────────

class ApiKey(Base):
    __tablename__ = "api_keys"

    id         = Column(String, primary_key=True, default=generate_api_key_id)
    key        = Column(String, unique=True, nullable=False)
    org_id     = Column(String, nullable=False)
    name       = Column(String, nullable=True)
    created_at = Column(Integer, default=unix_now)


# ─── Workers ────────────────────────────────────────────────

class Worker(Base):
    __tablename__ = "workers"

    id             = Column(String, primary_key=True, default=generate_worker_id)
    org_id         = Column(String, nullable=False)
    api_key_id     = Column(String, nullable=True)
    hostname       = Column(String, nullable=False)
    os             = Column(String, nullable=True)
    cpu_cores      = Column(Integer, nullable=True)
    ram_total_gb   = Column(Float, nullable=True)
    gpus           = Column(Text, default="[]")
    runtimes       = Column(Text, default="[]")
    status         = Column(String, default="online")
    last_heartbeat = Column(Integer, default=unix_now)
    created_at     = Column(Integer, default=unix_now)


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


class BatchAssignment(Base):
    __tablename__ = "batch_assignments"

    batch_id    = Column(String, primary_key=True)
    worker_id   = Column(String, nullable=False)
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


# ─── Legacy (kept for backward compat) ─────────────────────

class ProviderCapability(Base):
    __tablename__ = "provider_capabilities"

    worker_id         = Column(String, primary_key=True)
    provider_id       = Column(String, nullable=False)
    vram_total_gb     = Column(Float, default=0)
    vram_available_gb = Column(Float, default=0)
    loaded_models     = Column(String, default="[]")
    status            = Column(String, default="online")
    last_heartbeat    = Column(Integer, default=unix_now)