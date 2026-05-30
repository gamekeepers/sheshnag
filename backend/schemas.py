from pydantic import BaseModel
from typing import Optional


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
    input_file_id: str
    completion_window: str
    status: str
    output_file_id: Optional[str] = None
    error_file_id: Optional[str] = None
    created_at: int
    completed_at: Optional[int] = None
    request_counts: RequestCounts = RequestCounts()

    @classmethod
    def from_batch(cls, batch):
        return cls(
            id=batch.id,
            endpoint=batch.endpoint,
            input_file_id=batch.input_file_id,
            completion_window=batch.completion_window,
            status=batch.status,
            output_file_id=batch.output_file_id,
            error_file_id=batch.error_file_id,
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