"""
Finetune dataset validator — structurally mirrors batch_validator.py's
phased approach (file preconditions -> line parsing -> schema -> cross-file
-> persist), but for a different shape: training examples, not OpenAI
batch requests.

Format (one JSON object per line):
    {"messages": [{"role": "user", "content": "..."},
                   {"role": "assistant", "content": "..."}]}

This is the standard chat-SFT shape (TRL's SFTTrainer and
apply_chat_template both consume it directly) — deliberately the same
"messages" array shape /v1/chat/completions batch requests already use,
so a user who has written one has effectively written the other.
"""
import json
import logging
import os
from dataclasses import dataclass, field
from typing import List, Optional

from database import SessionLocal
from models import FinetuneJob, unix_now

logger = logging.getLogger(__name__)


MAX_LINES = 20_000
MAX_FILE_BYTES = 50 * 1024 * 1024  # 50 MB — training sets are text, not batch-scale
MAX_STORED_ERRORS = 100

VALID_ROLES = frozenset({"system", "user", "assistant"})


@dataclass(frozen=True)
class ValidationError:
    code: str
    line: Optional[int]
    field: Optional[str]
    message: str


@dataclass
class ValidationResult:
    valid: bool = True
    total_lines: int = 0
    errors: List[ValidationError] = field(default_factory=list)
    total_error_count: int = 0

    def add_error(self, err: ValidationError) -> None:
        self.valid = False
        self.total_error_count += 1
        if len(self.errors) < MAX_STORED_ERRORS:
            self.errors.append(err)

    @property
    def stored_errors(self) -> List[ValidationError]:
        return self.errors[:MAX_STORED_ERRORS]


def _fail(result: ValidationResult, code: str, line, field_, message: str) -> ValidationResult:
    result.add_error(ValidationError(code, line, field_, message))
    return result


def _validate_file_preconditions(filepath: str) -> ValidationResult:
    result = ValidationResult()
    if not os.path.exists(filepath):
        return _fail(result, "file_not_found", None, None, f"Dataset file not found: {filepath}")
    size = os.stat(filepath).st_size
    if size > MAX_FILE_BYTES:
        return _fail(result, "file_too_large", None, None,
                     f"File size ({size} bytes) exceeds maximum of {MAX_FILE_BYTES} bytes")
    return result


def _validate_line(entry: dict, line_number: int) -> List[ValidationError]:
    errors = []

    if set(entry.keys()) - {"messages"}:
        errors.append(ValidationError(
            "unknown_fields", line_number, None,
            f"Unknown top-level fields: {sorted(set(entry.keys()) - {'messages'})}",
        ))

    messages = entry.get("messages")
    if not isinstance(messages, list) or len(messages) < 2:
        errors.append(ValidationError(
            "invalid_type", line_number, "messages",
            "'messages' is required and must be an array of at least 2 turns",
        ))
        return errors

    for i, msg in enumerate(messages):
        if not isinstance(msg, dict):
            errors.append(ValidationError(
                "invalid_type", line_number, f"messages[{i}]",
                f"messages[{i}] must be an object",
            ))
            continue
        role = msg.get("role")
        if role not in VALID_ROLES:
            errors.append(ValidationError(
                "invalid_value", line_number, f"messages[{i}].role",
                f"messages[{i}].role must be one of {sorted(VALID_ROLES)}, got {role!r}",
            ))
        content = msg.get("content")
        if not isinstance(content, str) or not content.strip():
            errors.append(ValidationError(
                "missing_field", line_number, f"messages[{i}].content",
                f"messages[{i}].content is required and must be non-empty",
            ))

    # The last turn is the training target — a conversation that doesn't
    # end with an assistant reply has nothing for the model to learn to
    # produce, which usually means the file was built wrong rather than
    # that the user actually wants that.
    if messages and isinstance(messages[-1], dict) and messages[-1].get("role") != "assistant":
        errors.append(ValidationError(
            "invalid_value", line_number, "messages[-1].role",
            "the last message in each example must have role 'assistant' "
            "(that's the completion the model trains to produce)",
        ))

    return errors


def _persist_result(job_id: str, result: ValidationResult) -> bool:
    db = SessionLocal()
    try:
        job = db.query(FinetuneJob).filter(FinetuneJob.id == job_id).first()
        if not job:
            return False

        if not result.valid:
            job.status = "failed"
            job.error_details = json.dumps({
                "object": "list",
                "data": [{"line": e.line, "code": e.code, "field": e.field, "message": e.message}
                         for e in result.stored_errors],
                "total_errors": result.total_error_count,
            })
            job.completed_at = unix_now()
            logger.warning("Finetune job %s FAILED validation — %d error(s)", job_id, result.total_error_count)
        else:
            job.status = "validated"
            job.requested_at = unix_now()
            logger.info("Finetune job %s validated — %d examples", job_id, result.total_lines)

        db.commit()
        return result.valid

    except Exception:
        logger.exception("Failed to persist validation result for finetune job %s", job_id)
        db.rollback()
        try:
            job = db.query(FinetuneJob).filter(FinetuneJob.id == job_id).first()
            if job:
                job.status = "failed"
                job.error_details = json.dumps({"error": "Failed to persist validation result"})
                job.completed_at = unix_now()
                db.commit()
        except Exception:
            logger.exception("Failed to mark finetune job %s as failed", job_id)
            db.rollback()
        return False
    finally:
        db.close()


def validate_finetune_dataset(job_id: str, filepath: str) -> bool:
    """Full-file validation. Updates FinetuneJob.status to 'validated' or
    'failed'. Returns True if valid, False otherwise."""
    result = _validate_file_preconditions(filepath)
    if not result.valid:
        return _persist_result(job_id, result)

    with open(filepath, "r") as f:
        for i, line in enumerate(f, start=1):
            stripped = line.strip()
            if not stripped:
                continue

            result.total_lines += 1
            if result.total_lines > MAX_LINES:
                _fail(result, "too_many_examples", i, None,
                      f"Dataset exceeds maximum of {MAX_LINES} examples")
                break

            try:
                entry = json.loads(stripped)
            except json.JSONDecodeError as e:
                result.add_error(ValidationError("invalid_json", i, None, f"Invalid JSON: {e}"))
                continue

            if not isinstance(entry, dict):
                result.add_error(ValidationError("not_object", i, None, "Line is not a JSON object"))
                continue

            for err in _validate_line(entry, i):
                result.add_error(err)

    if result.total_lines == 0:
        _fail(result, "empty_file", None, None, "Dataset file contains no examples")

    return _persist_result(job_id, result)
