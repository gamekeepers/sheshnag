"""Forward schema migration for existing databases.

`Base.metadata.create_all()` creates missing tables but never adds columns to
tables that already exist (see docs/develop.md), so each model change that
adds a column ships an entry here. Migrations are pure-ADD and idempotent
(check-then-apply), run once at startup after `create_all()`, and apply on
both Postgres and SQLite (the two dialects in the deployment matrix).
"""

import logging

from sqlalchemy import inspect, text
from sqlalchemy import Integer

from database import get_engine
from models import Batch, UsageRecord  # noqa: F401 — ensure models are registered

logger = logging.getLogger(__name__)


class _Migration:
    def __init__(self, name, table, column, coltype):
        self.name = name
        self.table = table
        self.column = column
        self.coltype = coltype

    def apply(self, conn):
        existing = {c["name"] for c in inspect(conn).get_columns(self.table)}
        if self.column in existing:
            return
        statement = (
            f"ALTER TABLE {self.table} ADD COLUMN {self.column} "
            f"{self.coltype.compile(dialect=conn.dialect)}"
        )
        conn.execute(text(statement))
        logger.info("Migration applied: %s", self.name)


MIGRATIONS = [
    _Migration("batches.prompt_tokens", "batches", "prompt_tokens", Integer()),
    _Migration("batches.completion_tokens", "batches", "completion_tokens", Integer()),
    _Migration("batches.total_tokens", "batches", "total_tokens", Integer()),
]


def ensure_schema(engine=None):
    """Add any columns the deployed database does not have yet."""
    engine = engine or get_engine()
    with engine.begin() as conn:
        existing_tables = set(inspect(conn).get_table_names())
        for m in MIGRATIONS:
            try:
                if m.table in existing_tables:
                    m.apply(conn)
            except Exception:
                logger.exception("Schema migration %s failed", m.name)
