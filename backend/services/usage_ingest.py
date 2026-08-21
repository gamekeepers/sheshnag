import json
import logging
from typing import List, Dict, Any, Optional
from sqlalchemy import func
from database import SessionLocal, get_engine
from models import Batch, UsageRecord, generate_usage_id, unix_now

logger = logging.getLogger(__name__)


def ingest_usage_records(batch_id: str, filepath: str) -> None:
    """Parse output JSONL file and populate usage_records with per-prompt token usage.

    Upserts rows on (batch_id, custom_id) to ensure idempotency across worker
    retries and requeued batches. Recomputes rollup columns on the Batch.
    Runs asynchronously off the HTTP request path.
    """
    db = SessionLocal()
    try:
        batch = db.query(Batch).filter(Batch.id == batch_id).first()
        if not batch:
            logger.warning("Batch %s not found during usage ingestion", batch_id)
            return

        batch_model = batch.model
        records: List[Dict[str, Any]] = []

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                for line_num, line in enumerate(f, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        raw = json.loads(line)
                    except Exception as exc:
                        logger.warning("Malformed JSONL line %d in %s: %exc", line_num, filepath)
                        continue

                    custom_id = raw.get("custom_id")
                    if not custom_id:
                        continue

                    # Failed prompts or older outputs have no response or usage
                    response = raw.get("response")
                    if not isinstance(response, dict):
                        continue

                    usage = response.get("usage")
                    if not isinstance(usage, dict):
                        continue

                    # Defensive reads (embeddings omit completion_tokens)
                    prompt_tokens = int(usage.get("prompt_tokens") or 0)
                    completion_tokens = int(usage.get("completion_tokens") or 0)
                    total_tokens = int(usage.get("total_tokens") or (prompt_tokens + completion_tokens))

                    records.append({
                        "id": generate_usage_id(),
                        "batch_id": batch_id,
                        "custom_id": str(custom_id),
                        "model": batch_model,
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "total_tokens": total_tokens,
                        "created_at": unix_now(),
                    })
        except FileNotFoundError:
            logger.warning("Output file not found for batch %s at %s", batch_id, filepath)
            return

        if records:
            # Upsert into usage_records using dialect-specific ON CONFLICT DO UPDATE
            engine = get_engine()
            dialect_name = engine.dialect.name

            if dialect_name == "postgresql":
                from sqlalchemy.dialects.postgresql import insert as dialect_insert
            else:
                from sqlalchemy.dialects.sqlite import insert as dialect_insert

            # Bulk upsert in chunks of 500
            chunk_size = 500
            for i in range(0, len(records), chunk_size):
                chunk = records[i:i + chunk_size]
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
            db.commit()

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
