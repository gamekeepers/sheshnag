import json
import logging
from typing import Any, Dict, Iterator, List, Optional

from sqlalchemy import func

from database import SessionLocal, get_engine
from models import Batch, UsageRecord, generate_usage_id, unix_now

logger = logging.getLogger(__name__)

CHUNK_SIZE = 500


def _coerce_usage(usage: Dict[str, Any]) -> Optional[Dict[str, int]]:
    """Read the three token counts, or None if any of them is not a number.

    Providers occasionally emit a null, a string or an object where an int
    belongs. Returning None lets the caller skip just that record instead of
    letting an int() raise and abort the whole file.
    """
    try:
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
    except (TypeError, ValueError):
        return None

    # A provider that omits total_tokens (embeddings routinely do) still has a
    # derivable total, and deriving it keeps the batch rollups internally
    # consistent — a 0 total alongside a non-zero prompt count would not.
    raw_total = usage.get("total_tokens")
    if raw_total is None:
        total_tokens = prompt_tokens + completion_tokens
    else:
        try:
            total_tokens = int(raw_total)
        except (TypeError, ValueError):
            return None

    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


def _iter_usage_records(filepath: str, batch_id: str, batch_model: str) -> Iterator[Dict[str, Any]]:
    """Yield one usage row per usable line of the output JSONL.

    Every skip is local to its line: a malformed or unusable record must not
    cost the usage of the prompts around it.
    """
    with open(filepath, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue

            try:
                raw = json.loads(line)
            except Exception as exc:
                logger.warning("Malformed JSONL line %d in %s: %s", line_num, filepath, exc)
                continue

            # Valid JSON is not necessarily an object — a list, null or scalar
            # would blow up on .get().
            if not isinstance(raw, dict):
                logger.warning("Skipping non-object JSONL line %d in %s", line_num, filepath)
                continue

            custom_id = raw.get("custom_id")
            if not custom_id:
                continue

            # A result can carry both a response and an error: Ollama returns the
            # model output alongside JSON_PARSE_ERROR / SCHEMA_VIOLATION. The
            # daemon counts those as failed, so counting their tokens here would
            # inflate the rollup.
            if raw.get("error") is not None:
                continue

            # Failed prompts or older outputs have no response or usage
            response = raw.get("response")
            if not isinstance(response, dict):
                continue

            usage = response.get("usage")
            if not isinstance(usage, dict):
                continue

            counts = _coerce_usage(usage)
            if counts is None:
                logger.warning(
                    "Skipping line %d in %s: non-numeric token counts %r", line_num, filepath, usage
                )
                continue

            yield {
                "id": generate_usage_id(),
                "batch_id": batch_id,
                "custom_id": str(custom_id),
                "model": batch_model,
                "created_at": unix_now(),
                **counts,
            }


def _upsert_chunk(db, chunk: List[Dict[str, Any]]) -> None:
    """Bulk upsert on (batch_id, custom_id) so worker retries stay idempotent."""
    if get_engine().dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as dialect_insert
    else:
        from sqlalchemy.dialects.sqlite import insert as dialect_insert

    stmt = dialect_insert(UsageRecord).values(chunk)
    stmt = stmt.on_conflict_do_update(
        index_elements=["batch_id", "custom_id"],
        set_={
            "model": stmt.excluded.model,
            "prompt_tokens": stmt.excluded.prompt_tokens,
            "completion_tokens": stmt.excluded.completion_tokens,
            "total_tokens": stmt.excluded.total_tokens,
        },
    )
    db.execute(stmt)


def ingest_usage_records(batch_id: str, filepath: str) -> None:
    """Parse output JSONL file and populate usage_records with per-prompt token usage.

    Upserts rows on (batch_id, custom_id) to ensure idempotency across worker
    retries and requeued batches. Recomputes rollup columns on the Batch.
    Runs off the HTTP request path.

    Records are parsed and written a chunk at a time, so peak memory is bounded
    by CHUNK_SIZE rather than by the number of prompts in the batch.
    """
    db = SessionLocal()
    try:
        batch = db.query(Batch).filter(Batch.id == batch_id).first()
        if not batch:
            logger.warning("Batch %s not found during usage ingestion", batch_id)
            return

        wrote_any = False
        try:
            chunk: List[Dict[str, Any]] = []
            for record in _iter_usage_records(filepath, batch_id, batch.model):
                chunk.append(record)
                if len(chunk) >= CHUNK_SIZE:
                    _upsert_chunk(db, chunk)
                    db.commit()
                    wrote_any = True
                    chunk = []
            if chunk:
                _upsert_chunk(db, chunk)
                db.commit()
                wrote_any = True
        except FileNotFoundError:
            logger.warning("Output file not found for batch %s at %s", batch_id, filepath)
            return

        if not wrote_any:
            return

        # Recompute rollups from usage_records table
        totals = db.query(
            func.sum(UsageRecord.prompt_tokens).label("prompt_sum"),
            func.sum(UsageRecord.completion_tokens).label("completion_sum"),
            func.sum(UsageRecord.total_tokens).label("total_sum"),
        ).filter(UsageRecord.batch_id == batch_id).first()

        if totals and totals.total_sum is not None:
            batch.prompt_tokens = int(totals.prompt_sum or 0)
            batch.completion_tokens = int(totals.completion_sum or 0)
            batch.total_tokens = int(totals.total_sum or 0)
            db.commit()
            logger.info(
                "Ingested usage for batch %s: %d prompt, %d completion, %d total tokens",
                batch_id, batch.prompt_tokens, batch.completion_tokens, batch.total_tokens,
            )

    except Exception as exc:
        logger.exception("Error ingesting usage records for batch %s: %s", batch_id, exc)
        db.rollback()
    finally:
        db.close()
