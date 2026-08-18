"""Backend tests for the n>1 submission-time gate (issue #49).

Ankush's decision: n>1 in a batch targeting an Ollama-runtime model is
rejected at submission time (POST /v1/batches validation phase).  A vLLM-
runtime batch with n>1 must be accepted.  A batch without n must be
unaffected either way.

These tests call validate_batch_file() directly (the function that the
batches router dispatches to in a thread) rather than through the HTTP API
so we can control the filesystem and avoid auth complexity.

SessionLocal usage inside batch_validator is patched to avoid needing a
real DB, and get_catalog_entry is patched to return mock catalogue entries
with the desired runtime.

NOTE: backend/.gitignore ignores test_*.py — this file requires
`git add -f backend/tests/test_batch_n_validation.py` to be committed.
"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_jsonl_row(custom_id: str, n=None, url="/v1/chat/completions",
                    model="gemma3-12b-ollama") -> str:
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "hello"}],
    }
    if n is not None:
        body["n"] = n
    return json.dumps({
        "custom_id": custom_id,
        "method": "POST",
        "url": url,
        "body": body,
    })


def _write_jsonl(path: Path, rows: list[str]) -> None:
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def _mock_catalog_entry(runtime: str) -> MagicMock:
    """Return a MagicMock that looks like a ModelCatalog entry."""
    entry = MagicMock()
    entry.runtime = runtime
    entry.enabled = True
    entry.status = "active"
    return entry


def _run_validator(batch_id: str, filepath: str, runtime: str) -> bool:
    """Run validate_batch_file with patched DB and catalogue lookup.

    Returns the bool result of validate_batch_file (True=valid, False=failed).
    Also captures the ValidationResult by inspecting the DB write call.
    """
    from services.batch_validator import validate_batch_file

    mock_batch = MagicMock()
    mock_batch.completion_window = "24h"

    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = mock_batch
    mock_db_ctx = MagicMock()
    mock_db_ctx.__enter__ = MagicMock(return_value=mock_db)
    mock_db_ctx.__exit__ = MagicMock(return_value=False)

    catalog_entry = _mock_catalog_entry(runtime) if runtime is not None else None

    with patch("services.batch_validator.SessionLocal", return_value=mock_db), \
         patch("provider_picker.get_catalog_entry", return_value=catalog_entry):
        result = validate_batch_file(batch_id, filepath)

    return result, mock_batch


# ---------------------------------------------------------------------------
# Tests — Ollama + n>1 → rejected at submission
# ---------------------------------------------------------------------------

class TestOllamaNGt1RejectedAtSubmission:
    """Batches targeting an Ollama model with n>1 must be rejected."""

    def test_single_row_n3_rejected(self, tmp_path):
        """A single-row batch with n=3 targeting an ollama model must fail."""
        jsonl = tmp_path / "batch.jsonl"
        _write_jsonl(jsonl, [_make_jsonl_row("req-1", n=3)])

        valid, mock_batch = _run_validator("batch-abc", str(jsonl), runtime="ollama")

        assert not valid, "Expected batch to be rejected (n=3 on ollama)"
        # Batch status must be set to 'failed'
        assert mock_batch.status == "failed"
        # error_details must mention the parameter and the row
        error_raw = mock_batch.error_details
        assert error_raw is not None
        errors = json.loads(error_raw)
        assert errors["total_errors"] >= 1
        error_msg = errors["data"][0]["message"]
        assert "n>1" in error_msg or "n>1" in error_msg.replace("n>1", "n>1")
        assert "Ollama" in error_msg
        assert "line 1" in error_msg
        assert "n=3" in error_msg

    def test_multiple_rows_some_n_gt1_rejected(self, tmp_path):
        """Multiple rows — even one n>1 row rejects the whole batch."""
        jsonl = tmp_path / "batch.jsonl"
        rows = [
            _make_jsonl_row("req-1"),          # n absent — fine
            _make_jsonl_row("req-2", n=1),      # n=1 — fine
            _make_jsonl_row("req-3", n=2),      # n=2 — triggers rejection
            _make_jsonl_row("req-4", n=5),      # n=5 — also n>1
        ]
        _write_jsonl(jsonl, rows)

        valid, mock_batch = _run_validator("batch-abc", str(jsonl), runtime="ollama")

        assert not valid
        errors = json.loads(mock_batch.error_details)
        msg = errors["data"][0]["message"]
        # Both offending rows must be mentioned
        assert "line 3" in msg
        assert "n=2" in msg
        assert "line 4" in msg
        assert "n=5" in msg

    def test_rejection_error_has_correct_code(self, tmp_path):
        """Error code must be 'unsupported_parameter'."""
        jsonl = tmp_path / "batch.jsonl"
        _write_jsonl(jsonl, [_make_jsonl_row("req-1", n=4)])

        valid, mock_batch = _run_validator("batch-abc", str(jsonl), runtime="ollama")

        assert not valid
        errors = json.loads(mock_batch.error_details)
        code = errors["data"][0]["code"]
        assert code == "unsupported_parameter"

    def test_rejection_error_field_is_body_n(self, tmp_path):
        """Error field must be 'body.n'."""
        jsonl = tmp_path / "batch.jsonl"
        _write_jsonl(jsonl, [_make_jsonl_row("req-1", n=2)])

        valid, mock_batch = _run_validator("batch-abc", str(jsonl), runtime="ollama")

        assert not valid
        errors = json.loads(mock_batch.error_details)
        field = errors["data"][0]["field"]
        assert field == "body.n"

    def test_n_equals_1_on_ollama_accepted(self, tmp_path):
        """n=1 is the default and must not trigger the gate on Ollama."""
        jsonl = tmp_path / "batch.jsonl"
        _write_jsonl(jsonl, [_make_jsonl_row("req-1", n=1)])

        valid, mock_batch = _run_validator("batch-abc", str(jsonl), runtime="ollama")

        assert valid, f"Expected n=1 on ollama to be accepted; batch status={mock_batch.status}"
        assert mock_batch.status == "validated"

    def test_n_absent_on_ollama_accepted(self, tmp_path):
        """No n field at all must be accepted on Ollama (regression)."""
        jsonl = tmp_path / "batch.jsonl"
        _write_jsonl(jsonl, [_make_jsonl_row("req-1")])

        valid, mock_batch = _run_validator("batch-abc", str(jsonl), runtime="ollama")

        assert valid, f"Expected no-n on ollama to be accepted; status={mock_batch.status}"
        assert mock_batch.status == "validated"


# ---------------------------------------------------------------------------
# Tests — vLLM + n>1 → accepted
# ---------------------------------------------------------------------------

class TestVLLMNGt1Accepted:
    """Batches targeting a vLLM model with n>1 must be accepted."""

    def test_n3_on_vllm_accepted(self, tmp_path):
        """n=3 on a vllm model must pass through without rejection."""
        jsonl = tmp_path / "batch.jsonl"
        _write_jsonl(jsonl, [_make_jsonl_row("req-1", n=3, model="some-vllm-model")])

        valid, mock_batch = _run_validator("batch-abc", str(jsonl), runtime="vllm")

        assert valid, (
            f"Expected n=3 on vllm to be accepted; "
            f"batch status={mock_batch.status}, "
            f"errors={getattr(mock_batch, 'error_details', None)}"
        )
        assert mock_batch.status == "validated"

    def test_n5_on_vllm_accepted(self, tmp_path):
        """n=5 on a vllm model must be accepted."""
        jsonl = tmp_path / "batch.jsonl"
        _write_jsonl(jsonl, [_make_jsonl_row("req-1", n=5, model="some-vllm-model")])

        valid, mock_batch = _run_validator("batch-abc", str(jsonl), runtime="vllm")

        assert valid
        assert mock_batch.status == "validated"


# ---------------------------------------------------------------------------
# Tests — no n parameter → unaffected (regression)
# ---------------------------------------------------------------------------

class TestNoNParameterUnaffected:
    """Batches without n in any row must be unaffected regardless of runtime."""

    def test_no_n_on_ollama_is_valid(self, tmp_path):
        jsonl = tmp_path / "batch.jsonl"
        _write_jsonl(jsonl, [
            _make_jsonl_row("req-1"),
            _make_jsonl_row("req-2"),
        ])

        valid, mock_batch = _run_validator("batch-abc", str(jsonl), runtime="ollama")

        assert valid
        assert mock_batch.status == "validated"

    def test_no_n_on_vllm_is_valid(self, tmp_path):
        jsonl = tmp_path / "batch.jsonl"
        _write_jsonl(jsonl, [
            _make_jsonl_row("req-1", model="some-vllm-model"),
        ])

        valid, mock_batch = _run_validator("batch-abc", str(jsonl), runtime="vllm")

        assert valid
        assert mock_batch.status == "validated"
