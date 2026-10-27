"""The job forge-runner receives from POST /workers/finetune-poll.

Field names are the poll payload's (docs/finetune_contract.md, "Runner
protocol"). Unknown fields are ignored so the backend can add to the payload
without breaking deployed runners; missing required ones fail loudly.
"""
from dataclasses import dataclass, fields
from typing import Optional

ALL_LINEAR = "all-linear"


@dataclass(frozen=True)
class Job:
    job_id: str
    source_ref: str
    dataset_path: str
    source_revision: Optional[str] = None
    base_model_id: str = ""
    method: str = "supervised"
    validation_file_id: Optional[str] = None
    lora_rank: int = 16
    lora_alpha: int = 32
    lora_dropout: float = 0.05
    target_modules: str = ALL_LINEAR       # "all-linear" or comma-separated module names
    epochs: int = 3
    batch_size: int = 16                    # effective batch
    learning_rate: float = 2e-4
    max_seq_length: int = 1024
    seed: int = 42
    engine: str = "llamafactory"
    engine_image: Optional[str] = None

    @classmethod
    def from_payload(cls, payload: dict) -> "Job":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in payload.items() if k in known and v is not None})
