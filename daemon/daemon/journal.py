"""
Per-job result journal — what lets a job resume after the daemon dies.

Every result the executor produces is appended here as it completes, so a
``kill -9`` at prompt 900 of 1000 leaves 900 rows on disk instead of none. When
the same job (``job_id`` is the batch id, stable across requeue) runs again on
this worker, the rows already journalled are restored and not re-executed.

Format
------
One JSON object per line, each a serialised ``CompletionResult`` — exactly the
shape of an output-file row, and keyed by its ``custom_id``. No byte offsets,
run counters or worker-local paths: the completed set is sparse (the pool
finishes prompts out of order), and a set of rows keyed by ``custom_id`` is the
only shape another worker could later read as a checkpoint (#178).

A later line for the same ``custom_id`` replaces an earlier one.

What gets journalled
--------------------
Successes, and failures that would fail the same way again (``is_final``). A
row that failed deterministically was still executed, and re-running it on
every resume would retry it forever. A transient failure — a timeout, a 5xx or
429, a refused connection while the runtime restarts, an executor exception —
is *not* journalled, so a resume gets another go at it. That retry is bounded
anyway: each requeue spends one of the backend's MAX_BATCH_ATTEMPTS.

Durability: one ``flush()`` per unit of work, ``fsync()`` on close
------------------------------------------------------------------
The rows of each finished unit (one chat prompt, or one coalesced embedding
chunk) are written and flushed to the OS together. That survives what this
journal exists for: the daemon process dying (``kill -9``, OOM kill, crash),
because the kernel still holds the bytes and writes them back.

It does not survive the *machine* dying (power loss, kernel panic) before
writeback. We accept that rather than ``fsync()`` per unit, because losing a
few rows only means running them again. Nothing becomes incorrect.
``fsync()`` per unit would put a disk barrier on the event loop for every
prompt. The journal is fsynced once, on close.

The journal is a best-effort checkpoint, never a reason to lose work: if a write
fails (disk full, I/O error), it logs once and stops journalling, and the job
carries on exactly as it would have without one.

Torn lines
----------
A process killed mid-write can leave a truncated final line. Opening the
journal discards it, and anything else that does not parse, and logs it. It
never raises and never turns a fragment into a result. The file is then
truncated back to the last complete line before appending. Without that, the
next row would be glued onto the fragment and both would be lost.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import BinaryIO, Dict, Iterable, Optional

from daemon.models import CompletionResult

logger = logging.getLogger(__name__)

JOURNAL_FILENAME = "journal.jsonl"

# Error prefixes that describe the row itself (its output, its parameters, or
# what this runtime supports), so running it again gives the same answer.
_DETERMINISTIC_PREFIXES = (
    "JSON_PARSE_ERROR:",
    "SCHEMA_VIOLATION:",
    "EMPTY_RESPONSE:",
    "UNSUPPORTED_ENDPOINT:",
    "UNSUPPORTED_PARAMETER:",
    "VERSION_MISMATCH:",
    "MODEL_MISMATCH:",
)

# A 4xx from the runtime rejects the request itself — except the ones that mean
# "not now": 408 timeout, 409 conflict, 425 too early, 429 rate limited.
# Matches the vLLM / llama.cpp form ("HTTP 400 for prompt …") and httpx's own
# message, which Ollama passes through ("Client error '400 Bad Request' …").
_CLIENT_ERROR = re.compile(r"^(?:HTTP |Client error ')4(?!08|09|25|29)\d\d\b")


def is_final(result: CompletionResult) -> bool:
    """Whether a resume should keep this result rather than run the row again."""
    if result.is_success:
        return True
    error = result.error or ""
    return error.startswith(_DETERMINISTIC_PREFIXES) or bool(_CLIENT_ERROR.match(error))


def _parse_line(raw: bytes) -> Optional[CompletionResult]:
    if not raw.strip():
        return None
    try:
        return CompletionResult.model_validate_json(raw)
    except Exception:
        return None


def _scan(path: Path) -> tuple[Dict[str, CompletionResult], int, bool]:
    """Stream the journal line by line.

    Returns (results by custom_id, byte length of the intact prefix, whether
    the file ends without a newline but its last line parsed). Memory is the
    parsed results plus one line, never the whole file.
    """
    results: Dict[str, CompletionResult] = {}
    intact = 0
    needs_newline = False

    with open(path, "rb") as fh:
        offset = 0
        for line in fh:
            start = offset
            offset += len(line)

            if not line.endswith(b"\n"):
                # Final line with no newline: the process died mid-write. It is
                # usually a fragment. Keep it only if it parses whole.
                result = _parse_line(line)
                if result is not None:
                    results[result.custom_id] = result
                    intact = offset
                    needs_newline = True
                else:
                    logger.warning(
                        f"Journal {path}: discarding torn final line ({len(line)} bytes)"
                    )
                break

            intact = offset
            result = _parse_line(line)
            if result is None:
                if line.strip():
                    logger.warning(f"Journal {path}: skipping unreadable line at byte {start}")
                continue
            results[result.custom_id] = result

    return results, intact, needs_newline


def load(path: Path) -> Dict[str, CompletionResult]:
    """Results already journalled at ``path``, by custom_id. Empty if none."""
    if not path.exists():
        return {}
    results, _, _ = _scan(path)
    return results


class ResultJournal:
    """Append-only writer for one job's journal. Use ``open`` to create."""

    def __init__(self, path: Path, fh: Optional[BinaryIO], restored: Dict[str, CompletionResult]):
        self.path = path
        self._fh = fh
        self.restored = restored

    @classmethod
    def open(cls, path: Path) -> "ResultJournal":
        """Open the journal for appending, recovering what an earlier run left.

        ``restored`` holds the rows already on disk. A torn final line is cut
        off first, so appended rows always start on a fresh line. Never raises:
        a journal that cannot be read or opened just means nothing to resume
        and nothing journalled.
        """
        restored: Dict[str, CompletionResult] = {}
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                restored, intact, needs_newline = _scan(path)
                if intact < path.stat().st_size:
                    with open(path, "r+b") as fh:
                        fh.truncate(intact)
                if needs_newline:
                    with open(path, "ab") as fh:
                        fh.write(b"\n")
            return cls(path, open(path, "ab"), restored)
        except OSError as exc:
            logger.warning(f"Journal {path}: cannot open, running without one: {exc}")
            return cls(path, None, restored)

    def append(self, results: Iterable[CompletionResult]) -> None:
        """Persist finished rows, flushed to the OS once before returning."""
        if self._fh is None:
            return
        try:
            for result in results:
                self._fh.write(result.model_dump_json().encode("utf-8") + b"\n")
            self._fh.flush()
        except OSError as exc:
            # Stop here rather than fail executed work. Whatever was written
            # before this is intact; a partial last line is cut on next open.
            logger.error(
                f"Journal {self.path}: write failed, journalling disabled for "
                f"the rest of this job: {exc}"
            )
            self._close_quietly()

    def close(self) -> None:
        if self._fh is None:
            return
        try:
            self._fh.flush()
            os.fsync(self._fh.fileno())
        except OSError as exc:
            logger.warning(f"Journal {self.path}: fsync on close failed: {exc}")
        finally:
            self._close_quietly()

    def _close_quietly(self) -> None:
        fh, self._fh = self._fh, None
        if fh is not None:
            try:
                fh.close()
            except OSError:
                pass
