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

Durability: ``flush()`` per row, ``fsync()`` on close
------------------------------------------------------
Each row is flushed to the OS as soon as it is written. That survives what this
journal exists for: the daemon process dying (``kill -9``, OOM kill, crash),
because the kernel still holds the bytes and writes them back.

It does not survive the *machine* dying (power loss, kernel panic) before
writeback. We accept that rather than ``fsync()`` per row, because the cost of
losing a few rows is only re-running them. Nothing becomes incorrect.
``fsync()`` per row would put a disk barrier on every row: cheap next to a
multi-second chat completion, but a real tax on embedding chunks, which finish
dozens of rows at once. The journal is fsynced once, on close.

Torn lines
----------
A process killed mid-write can leave a truncated final line. ``load`` discards
it, and anything else that does not parse, and logs it. It never raises and
never turns a fragment into a result. ``open`` then truncates the file back to
the last complete line before appending. Without that, the next row would be
glued onto the fragment and both would be lost.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import BinaryIO, Dict, Optional

from daemon.models import CompletionResult

logger = logging.getLogger(__name__)

JOURNAL_FILENAME = "journal.jsonl"


def _parse_line(raw: bytes) -> Optional[CompletionResult]:
    if not raw.strip():
        return None
    try:
        return CompletionResult.model_validate_json(raw)
    except Exception:
        return None


def _scan(path: Path) -> tuple[Dict[str, CompletionResult], int, bool]:
    """Read the journal.

    Returns (results by custom_id, byte length of the intact prefix, whether
    the file ends without a newline but its last line parsed).
    """
    data = path.read_bytes()
    results: Dict[str, CompletionResult] = {}
    pos = 0
    intact = 0
    needs_newline = False

    while pos < len(data):
        nl = data.find(b"\n", pos)
        if nl == -1:
            # Final line with no newline: the process died mid-write. It is
            # usually a fragment. Keep it only if it parses whole.
            tail = data[pos:]
            result = _parse_line(tail)
            if result is not None:
                results[result.custom_id] = result
                intact = len(data)
                needs_newline = True
            else:
                logger.warning(
                    f"Journal {path}: discarding torn final line "
                    f"({len(tail)} bytes)"
                )
            break

        line = data[pos:nl]
        pos = nl + 1
        intact = pos
        result = _parse_line(line)
        if result is None:
            if line.strip():
                logger.warning(f"Journal {path}: skipping unreadable line at byte {pos - len(line) - 1}")
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

    def __init__(self, path: Path, fh: BinaryIO, restored: Dict[str, CompletionResult]):
        self.path = path
        self._fh = fh
        self.restored = restored

    @classmethod
    def open(cls, path: Path) -> "ResultJournal":
        """Open the journal for appending, recovering what an earlier run left.

        ``restored`` holds the rows already on disk. A torn final line is cut
        off first, so appended rows always start on a fresh line.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        restored: Dict[str, CompletionResult] = {}
        if path.exists():
            restored, intact, needs_newline = _scan(path)
            if intact < path.stat().st_size:
                with open(path, "r+b") as fh:
                    fh.truncate(intact)
            if needs_newline:
                with open(path, "ab") as fh:
                    fh.write(b"\n")

        fh = open(path, "ab")
        return cls(path, fh, restored)

    def append(self, result: CompletionResult) -> None:
        """Persist one finished row. Flushed to the OS before returning."""
        self._fh.write(result.model_dump_json().encode("utf-8") + b"\n")
        self._fh.flush()

    def close(self) -> None:
        if self._fh.closed:
            return
        try:
            self._fh.flush()
            os.fsync(self._fh.fileno())
        except OSError as exc:
            logger.warning(f"Journal {self.path}: fsync on close failed: {exc}")
        finally:
            self._fh.close()
