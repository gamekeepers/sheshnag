from database import Base
from sqlalchemy import Column, String, DateTime
from datetime import datetime, timezone
import uuid


class Job(Base):
    __tablename__ = "jobs"

    id          = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    status      = Column(String, default="queued")   # queued | running | completed | failed
    input_path  = Column(String, nullable=True)
    output_path = Column(String, nullable=True)
    created_at  = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class JobAssignment(Base):
    __tablename__ = "job_assignments"

    job_id      = Column(String, primary_key=True)
    worker_id   = Column(String, nullable=False)
    assigned_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))