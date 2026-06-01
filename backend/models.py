from database import Base
from sqlalchemy import Column, String, Integer
from datetime import datetime, timezone
import uuid


def generate_file_id():
    return f"file-{uuid.uuid4().hex[:24]}"


def generate_batch_id():
    return f"batch-{uuid.uuid4().hex[:24]}"


def unix_now():
    return int(datetime.now(timezone.utc).timestamp())


class File(Base):
    __tablename__ = "files"

    id         = Column(String, primary_key=True, default=generate_file_id)
    filename   = Column(String, nullable=False)
    purpose    = Column(String, default="batch")
    bytes      = Column(Integer, default=0)
    filepath   = Column(String, nullable=False, default="")
    created_at = Column(Integer, default=unix_now)


class Batch(Base):
    __tablename__ = "batches"

    id                       = Column(String, primary_key=True, default=generate_batch_id)
    endpoint                 = Column(String, nullable=False)
    input_file_id            = Column(String, nullable=False)
    completion_window        = Column(String, default="24h")
    status                   = Column(String, default="validating")
    output_file_id           = Column(String, nullable=True)
    error_file_id            = Column(String, nullable=True)
    created_at               = Column(Integer, default=unix_now)
    completed_at             = Column(Integer, nullable=True)
    request_counts_total     = Column(Integer, default=0)
    request_counts_completed = Column(Integer, default=0)
    request_counts_failed    = Column(Integer, default=0)


class BatchAssignment(Base):
    __tablename__ = "batch_assignments"

    batch_id    = Column(String, primary_key=True)
    worker_id   = Column(String, nullable=False)
    assigned_at = Column(Integer, default=unix_now)