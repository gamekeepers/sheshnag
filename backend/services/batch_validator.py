import json
import logging
import os
from dataclasses import dataclass, field
from typing import List, Optional

from database import SessionLocal
from models import Batch, unix_now


# ── Logging ──────────────────────────────────────────────────────────────────

logger = logging.getLogger(__name__)


# ── Constants ────────────────────────────────────────────────────────────────

MAX_LINES = 50_000
MAX_FILE_BYTES = 100 * 1024 * 1024  # 100 MB
MAX_STORED_ERRORS = 100

SUPPORTED_ENDPOINTS = frozenset({
    "/v1/chat/completions",
    "/v1/completions",
    "/v1/embeddings",
})

# Body fields that only some runtimes honour, and the capability each needs.
# Collected per file; the batch then carries the set and the scheduler offers
# it only to a runtime advertising all of them.
CAPABILITY_FIELDS = {
    "logprobs": "body.logprobs",
    "completions": "url",
    "prompt_scoring": "body.echo",
}

ALLOWED_TOP_LEVEL_KEYS = frozenset({
    "custom_id",
    "method",
    "url",
    "body",
})

REQUIRED_BODY_KEYS = {"model"}


# ── Structured Error Types ──────────────────────────────────────────────────

@dataclass(frozen=True)
class ValidationError:
    """A single validation issue at a specific line and field."""
    code: str              # e.g. "missing_field", "invalid_json", "duplicate_custom_id"
    line: Optional[int]    # line number (None for file-level errors)
    field: Optional[str]   # e.g. "body.model", "method", None for JSON parse errors
    message: str           # human-readable, API-style ("body.model is required")


@dataclass
class ValidationResult:
    """Aggregated outcome of validating a batch file."""
    valid: bool = True
    model: Optional[str] = None
    # Runtime capabilities the rows ask for; persisted on the batch.
    required_capabilities: List[str] = field(default_factory=list)
    total_lines: int = 0
    errors: List[ValidationError] = field(default_factory=list)
    total_error_count: int = 0       # counts ALL errors even beyond storage cap

    def add_error(self, err: ValidationError) -> None:
        self.valid = False
        self.total_error_count += 1
        if len(self.errors) < MAX_STORED_ERRORS:
            self.errors.append(err)

    @property
    def stored_errors(self) -> List[ValidationError]:
        """Return errors capped at MAX_STORED_ERRORS."""
        return self.errors[:MAX_STORED_ERRORS]


# ── Helper ──────────────────────────────────────────────────────────────────

def _fail(result: ValidationResult, code: str, line: Optional[int], field_: Optional[str], message: str) -> ValidationResult:
    """Convenience: add a single error and return the result."""
    result.add_error(ValidationError(code, line, field_, message))
    return result


# ── Phase 1 — FileValidator ─────────────────────────────────────────────────

def _validate_file_preconditions(filepath: str) -> ValidationResult:
    """Checks that run BEFORE opening the file."""
    result = ValidationResult()

    if not os.path.exists(filepath):
        return _fail(result, "file_not_found", None, None, f"Input file not found: {filepath}")

    size = os.stat(filepath).st_size
    if size > MAX_FILE_BYTES:
        return _fail(result, "file_too_large", None, None,
                     f"File size ({size} bytes) exceeds maximum of {MAX_FILE_BYTES} bytes")

    return result


# ── Phase 2 — LineParser ────────────────────────────────────────────────────

def _parse_line(line_number: int, raw_line: str):
    """Try to decode a single line. Returns (line_number, parsed_dict | None, error | None)."""
    try:
        entry = json.loads(raw_line)
    except json.JSONDecodeError as e:
        return line_number, None, ValidationError("invalid_json", line_number, None, f"Invalid JSON: {e}")

    if not isinstance(entry, dict):
        return line_number, None, ValidationError("not_object", line_number, None, "Line is not a JSON object")

    return line_number, entry, None


# ── Phase 3 — SchemaValidator ──────────────────────────────────────────────

def _validate_no_extra_keys(entry: dict, line_number: int) -> List[ValidationError]:
    extra = set(entry.keys()) - ALLOWED_TOP_LEVEL_KEYS
    if extra:
        return [ValidationError(
            "unknown_fields", line_number, None,
            f"Unknown top-level fields: {sorted(extra)}"
        )]
    return []


def _validate_custom_id(entry: dict, line_number: int) -> List[ValidationError]:
    value = entry.get("custom_id")
    if not isinstance(value, str) or not value:
        return [ValidationError("missing_field", line_number, "custom_id", "'custom_id' is required and must be a string")]
    return []


def _validate_method(entry: dict, line_number: int) -> List[ValidationError]:
    value = entry.get("method")
    if not isinstance(value, str) or not value:
        return [ValidationError("missing_field", line_number, "method", "'method' is required")]
    if value != "POST":
        return [ValidationError("invalid_method", line_number, "method", "'method' must be 'POST'")]
    return []


def _validate_url(entry: dict, line_number: int) -> List[ValidationError]:
    value = entry.get("url")
    if not isinstance(value, str) or not value:
        return [ValidationError("missing_field", line_number, "url", "'url' is required and must be a string")]
    if value not in SUPPORTED_ENDPOINTS:
        return [ValidationError(
            "unsupported_endpoint", line_number, "url",
            f"Endpoint '{value}' is not supported. Allowed: {sorted(SUPPORTED_ENDPOINTS)}"
        )]
    return []


def _validate_body(entry: dict, line_number: int) -> List[ValidationError]:
    value = entry.get("body")
    if not isinstance(value, dict):
        return [ValidationError("missing_field", line_number, "body", "'body' is required and must be an object")]
    missing = REQUIRED_BODY_KEYS - set(value.keys())
    if missing:
        errors = []
        for key in sorted(missing):
            errors.append(ValidationError("missing_field", line_number, f"body.{key}", f"body.{key} is required"))
        return errors
    return []


def _validate_model_type(entry: dict, line_number: int) -> List[ValidationError]:
    body = entry.get("body")
    if not isinstance(body, dict):
        return []  # already caught by _validate_body
    model = body.get("model")
    if model is not None and not isinstance(model, str):
        return [ValidationError("invalid_type", line_number, "body.model", "'body.model' must be a string")]
    return []


SCHEMA_VALIDATORS = [
    _validate_no_extra_keys,
    _validate_custom_id,
    _validate_method,
    _validate_url,
    _validate_body,
    _validate_model_type,
]


def _validate_schema(entry: dict, line_number: int) -> List[ValidationError]:
    """Run all schema validators on a parsed JSON object."""
    errors = []
    for validator in SCHEMA_VALIDATORS:
        errors.extend(validator(entry, line_number))
    return errors


# ── Phase 4 — EndpointValidator ────────────────────────────────────────────

def _validate_chat_body(body: dict, line_number: int) -> List[ValidationError]:
    """Validate /v1/chat/completions request body."""
    errors = []

    messages = body.get("messages")
    if not isinstance(messages, list) or len(messages) == 0:
        errors.append(ValidationError(
            "invalid_type", line_number, "body.messages",
            "body.messages is required and must be a non-empty array"
        ))
    else:
        for i, msg in enumerate(messages):
            if not isinstance(msg, dict):
                errors.append(ValidationError(
                    "invalid_type", line_number, f"body.messages[{i}]",
                    f"body.messages[{i}] must be an object"
                ))
                continue
            role = msg.get("role")
            if not isinstance(role, str) or not role:
                errors.append(ValidationError(
                    "missing_field", line_number, f"body.messages[{i}].role",
                    f"body.messages[{i}].role is required"
                ))
            content = msg.get("content")
            if content is None or not isinstance(content, str):
                errors.append(ValidationError(
                    "missing_field", line_number, f"body.messages[{i}].content",
                    f"body.messages[{i}].content is required"
                ))

    errors.extend(_validate_logprob_fields(body, line_number, chat=True))
    return errors


def _validate_logprob_fields(body: dict, line_number: int, *, chat: bool) -> List[ValidationError]:
    """OpenAI's shapes: chat takes `logprobs: bool` + `top_logprobs: 0..20`;
    completions takes `logprobs: 0..20` (an int) and `echo: bool`."""
    errors = []
    logprobs = body.get("logprobs")
    if logprobs is not None:
        if chat and not isinstance(logprobs, bool):
            errors.append(ValidationError("invalid_type", line_number, "body.logprobs",
                                          "body.logprobs must be a boolean on /v1/chat/completions"))
        if not chat and not (isinstance(logprobs, bool) or (isinstance(logprobs, int) and 0 <= logprobs <= 20)):
            errors.append(ValidationError("invalid_type", line_number, "body.logprobs",
                                          "body.logprobs must be an integer 0-20 on /v1/completions"))
    top = body.get("top_logprobs")
    if top is not None and not (isinstance(top, int) and not isinstance(top, bool) and 0 <= top <= 20):
        errors.append(ValidationError("invalid_type", line_number, "body.top_logprobs",
                                      "body.top_logprobs must be an integer 0-20"))
    echo = body.get("echo")
    if echo is not None and not isinstance(echo, bool):
        errors.append(ValidationError("invalid_type", line_number, "body.echo",
                                      "body.echo must be a boolean"))
    return errors


def _validate_completions_body(body: dict, line_number: int) -> List[ValidationError]:
    """Validate /v1/completions request body: a prompt, not messages."""
    errors = []
    prompt = body.get("prompt")
    ok = isinstance(prompt, str) and prompt != ""
    ok = ok or (isinstance(prompt, list) and len(prompt) > 0 and all(isinstance(p, str) for p in prompt))
    if not ok:
        errors.append(ValidationError(
            "invalid_type", line_number, "body.prompt",
            "body.prompt is required and must be a non-empty string or array of strings"
        ))
    errors.extend(_validate_logprob_fields(body, line_number, chat=False))
    return errors


def wanted_capabilities(url: str, body: dict) -> List[str]:
    """Which runtime capabilities one row asks for, in CAPABILITY_FIELDS order."""
    wants = []
    top = body.get("top_logprobs")
    if body.get("logprobs") or (isinstance(top, int) and not isinstance(top, bool) and top > 0):
        wants.append("logprobs")
    if url == "/v1/completions":
        wants.append("completions")
        if body.get("echo"):
            wants.append("prompt_scoring")
    return wants


ENDPOINT_VALIDATORS = {
    "/v1/chat/completions": _validate_chat_body,
    "/v1/completions": _validate_completions_body,
}


def _validate_endpoint_specific(entry: dict, line_number: int) -> List[ValidationError]:
    """Dispatch to endpoint-specific body validator."""
    url = entry.get("url", "")
    validator = ENDPOINT_VALIDATORS.get(url)
    if validator is None:
        return []  # no validator for this endpoint — skip deep validation
    body = entry.get("body")
    if not isinstance(body, dict):
        return []  # already caught by schema validator
    return validator(body, line_number)


# ── Phase 5 — CrossFileValidator ───────────────────────────────────────────

class _CrossFileContext:
    """Mutable state accumulated across all lines for cross-line checks."""

    def __init__(self):
        self.seen_custom_ids = set()
        self.first_model = None
        # Rows where body.n > 1, as (line_number, n_value) tuples.
        # Used for runtime-gated n>1 rejection at submission time (issue #49).
        self.n_gt1_rows: list = []
        # capability -> line where a row first asked for it, for the
        # capability gate (phase 4c) and the message it writes.
        self.required_capabilities: dict = {}

    def note_capabilities(self, line_number: int, url, body) -> None:
        if not isinstance(url, str) or not isinstance(body, dict):
            return
        for cap in wanted_capabilities(url, body):
            self.required_capabilities.setdefault(cap, line_number)

    def check_custom_id(self, line_number: int, custom_id) -> List[ValidationError]:
        if not isinstance(custom_id, str) or not custom_id:
            return []  # already caught by schema validator
        if custom_id in self.seen_custom_ids:
            return [ValidationError(
                "duplicate_custom_id", line_number, "custom_id",
                f"Duplicate custom_id '{custom_id}' (first seen earlier)"
            )]
        self.seen_custom_ids.add(custom_id)
        return []

    def check_model_consistency(self, line_number: int, model: str) -> List[ValidationError]:
        """Enforce single-model constraint."""
        if self.first_model is None:
            self.first_model = model
            return []
        if model != self.first_model:
            return [ValidationError(
                "model_mismatch", line_number, "body.model",
                f"Batch contains mixed models. Expected '{self.first_model}', got '{model}'"
            )]
        return []


# ── Phase 6/7 — Main validation + Persistence ──────────────────────────────

def _set_expires_at(batch: Batch) -> None:
    """Parse completion_window like '24h' or '30m' and set expires_at timestamp."""
    window = batch.completion_window or "24h"
    hours = 24
    try:
        value = int(window.replace("h", "").replace("m", ""))
        if window.endswith("m"):
            hours = round(value / 60, 1)
        else:
            hours = value
    except ValueError:
        pass
    batch.expires_at = unix_now() + int(hours * 3600)


def _online_hosts(db, entry):
    """Online workers (by heartbeat, not the sweeper's lagging flag) that
    could be given a plain-chat batch for `entry`."""
    from sqlalchemy.orm import joinedload
    from models import Worker, WorkerRuntime
    from scheduler import can_serve
    from sweeper import HEARTBEAT_TIMEOUT_SECONDS

    cutoff = unix_now() - HEARTBEAT_TIMEOUT_SECONDS
    workers = (
        db.query(Worker)
        .options(joinedload(Worker.runtimes).joinedload(WorkerRuntime.models))
        .filter(Worker.status == "online", Worker.last_heartbeat >= cutoff)
        .all()
    )
    return [w for w in workers if can_serve(entry, w)]


def _persist_result(batch_id: str, result: ValidationResult) -> bool:
    """Update batch status in a tight DB transaction."""
    db = SessionLocal()
    try:
        batch = db.query(Batch).filter(Batch.id == batch_id).first()
        if not batch:
            return False

        batch.request_counts_total = result.total_lines

        if not result.valid:
            batch.status = "failed"
            batch.error_details = json.dumps({
                "object": "list",
                "data": [{"line": e.line, "code": e.code, "field": e.field, "message": e.message}
                         for e in result.stored_errors],
                "total_errors": result.total_error_count,
            })
            batch.completed_at = unix_now()
            logger.warning("Batch %s FAILED — %d error(s)", batch_id, result.total_error_count)
        else:
            batch.status = "validated"
            batch.model = result.model or batch.model
            batch.required_capabilities = result.required_capabilities or None
            batch.requested_at = unix_now()
            _set_expires_at(batch)
            logger.info("Batch %s validated — %d requests, model=%s", batch_id, result.total_lines, result.model)

        db.commit()
        return result.valid

    except Exception:
        logger.exception("Failed to persist validation result for batch %s", batch_id)
        db.rollback()
        try:
            batch = db.query(Batch).filter(Batch.id == batch_id).first()
            if batch:
                batch.status = "failed"
                batch.error_details = json.dumps({"error": "Failed to persist validation result"})
                batch.completed_at = unix_now()
                db.commit()
        except Exception:
            logger.exception("Failed to mark batch %s as failed", batch_id)
            db.rollback()
        return False
    finally:
        db.close()


def validate_batch_file(batch_id: str, filepath: str) -> bool:
    """Full-file validation. Updates DB status to 'validated' or 'failed'.

    Returns True if valid, False otherwise.
    """
    # 1. File preconditions (no DB needed)
    result = _validate_file_preconditions(filepath)
    if not result.valid:
        return _persist_result(batch_id, result)

    # 2. Read and validate ALL lines in-memory (no DB session)
    context = _CrossFileContext()
    with open(filepath, "r") as f:
        for i, line in enumerate(f, start=1):
            stripped = line.strip()
            if not stripped:
                continue

            result.total_lines += 1
            if result.total_lines > MAX_LINES:
                _fail(result, "too_many_requests", i, None,
                      f"File exceeds maximum of {MAX_LINES} requests")
                break

            # Parse
            _, entry, parse_err = _parse_line(i, stripped)
            if parse_err:
                result.add_error(parse_err)
                continue

            # Schema validation
            errors = _validate_schema(entry, i)
            for err in errors:
                result.add_error(err)

            # Endpoint-specific body validation (only if no schema errors)
            if not errors:
                ep_errors = _validate_endpoint_specific(entry, i)
                for err in ep_errors:
                    result.add_error(err)

            # Cross-file checks (duplicate custom_id, model consistency)
            if entry:
                custom_id = entry.get("custom_id")
                body = entry.get("body")
                model = body.get("model") if isinstance(body, dict) else None
                cid_errors = context.check_custom_id(i, custom_id)
                for err in cid_errors:
                    result.add_error(err)
                if model and isinstance(model, str):
                    model_errors = context.check_model_consistency(i, model)
                    for err in model_errors:
                        result.add_error(err)

                # Collect rows with n > 1 for runtime-gated rejection (phase 4b).
                # We track these regardless of runtime so the check in phase 4b
                # is a simple list scan rather than a second file pass.
                if isinstance(body, dict):
                    n_value = body.get("n")
                    if n_value is not None and n_value != 1:
                        context.n_gt1_rows.append((i, n_value))
                context.note_capabilities(i, entry.get("url"), body)

            # Track model from first valid line
            if result.model is None and model and isinstance(model, str):
                result.model = model

    # 3. Empty file check
    if result.total_lines == 0:
        _fail(result, "empty_file", None, None, "Batch file contains no requests")

    # 4. Model must be a selectable catalogue entry (reproducibility gate:
    #    users pick a pinned model, not a free-form id).
    if result.valid and result.model:
        from scheduler import can_serve, get_catalog_entry
        db = SessionLocal()
        try:
            entry = get_catalog_entry(db, result.model)
            if entry is None:
                _fail(
                    result, "unsupported_model", None, "body.model",
                    f"Model '{result.model}' is not in the platform catalogue. "
                    f"Choose one of the ids from GET /v1/models.",
                )
            else:
                # 4b. Runtime-specific n>1 gate (issue #49, Ankush's decision).
                #
                # Ollama produces exactly 1 completion per request — n>1 in the
                # body is a hard executor-level error. Catching it here (at
                # submission time) gives the caller an immediate, actionable
                # rejection instead of a per-row failure discovered hours later.
                #
                # vLLM supports n natively; no restriction there.
                if entry.runtime == "ollama" and context.n_gt1_rows:
                    row_descriptions = ", ".join(
                        f"line {ln} (n={nv})" for ln, nv in context.n_gt1_rows
                    )
                    _fail(
                        result,
                        "unsupported_parameter",
                        None,
                        "body.n",
                        f"Ollama does not support n>1 (produces exactly 1 completion "
                        f"per request). Found n>1 in {len(context.n_gt1_rows)} row(s): "
                        f"{row_descriptions}. Use a vLLM-runtime model to submit "
                        f"batches with n>1, or remove the n parameter.",
                    )

                # 4c. Capability gate. A row may ask for something only some
                # runtimes honour (logprobs, /v1/completions, echo). When an
                # online worker hosts the model but none of those hosts
                # advertises the capability, fail now with the reason; when no
                # host is online at all, queue as usual and let the scheduler
                # hold the batch for a capable one.
                needs = list(context.required_capabilities)
                if result.valid and needs:
                    hosts = _online_hosts(db, entry)
                    if hosts and not any(can_serve(entry, w, needs) for w in hosts):
                        missing = [c for c in needs if not any(can_serve(entry, w, [c]) for w in hosts)]
                        first = missing[0]
                        _fail(
                            result,
                            "unsupported_capability",
                            context.required_capabilities[first],
                            CAPABILITY_FIELDS[first],
                            f"No online runtime serving '{result.model}' supports "
                            f"{', '.join(missing)}. Pick a model on a runtime that does "
                            f"(vLLM serves all three; Ollama ≥ 0.12.11 returns logprobs "
                            f"on chat), or remove the field.",
                        )
                if result.valid:
                    result.required_capabilities = needs
        finally:
            db.close()

    # 5. Tight DB transaction — only runs AFTER full validation
    return _persist_result(batch_id, result)
