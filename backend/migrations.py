"""Forward schema migration for existing databases.

`Base.metadata.create_all()` creates missing tables but never adds columns to
tables that already exist (see docs/develop.md), so each model change that
adds a column ships an entry here. Migrations are pure-ADD and idempotent,
run once at startup after `create_all()`, and apply on both Postgres and
SQLite (the two dialects in the deployment matrix).
"""

import logging

from sqlalchemy import inspect, text
from sqlalchemy import Integer, String

from database import get_engine
from models import Base, Batch, BatchAssignment, UsageRecord  # noqa: F401 — ensure models are registered

logger = logging.getLogger(__name__)


class _Migration:
    def __init__(self, name, table, column, coltype):
        self.name = name
        self.table = table
        self.column = column
        self.coltype = coltype

    def apply(self, conn):
        coltype = self.coltype.compile(dialect=conn.dialect)

        # Postgres has idempotent DDL, so use it rather than check-then-ALTER:
        # with more than one uvicorn worker booting at once, both would see the
        # column missing and the loser of the race would fail on "column
        # already exists".
        if conn.dialect.name == "postgresql":
            conn.execute(text(
                f"ALTER TABLE {self.table} ADD COLUMN IF NOT EXISTS "
                f"{self.column} {coltype}"
            ))
            logger.info("Migration ensured: %s", self.name)
            return

        # SQLite has no IF NOT EXISTS on ADD COLUMN — and no concurrent boot to
        # race with either, so check-then-apply is safe here.
        existing = {c["name"] for c in inspect(conn).get_columns(self.table)}
        if self.column in existing:
            return
        conn.execute(text(
            f"ALTER TABLE {self.table} ADD COLUMN {self.column} {coltype}"
        ))
        logger.info("Migration applied: %s", self.name)


MIGRATIONS = [
    _Migration("batches.prompt_tokens", "batches", "prompt_tokens", Integer()),
    _Migration("batches.completion_tokens", "batches", "completion_tokens", Integer()),
    _Migration("batches.total_tokens", "batches", "total_tokens", Integer()),
    # Added with the provider-record snapshot on BatchAssignment. Modelled
    # NOT NULL, but added nullable here: existing rows predate the columns and
    # have nothing to backfill from. Without these, every /workers/poll INSERT
    # raises UndefinedColumn on a database created before they landed — the
    # whole queue stops, because poll is the only path out of "validated".
    _Migration("batch_assignments.org_id",
               "batch_assignments", "org_id", String()),
    _Migration("batch_assignments.worker_hostname",
               "batch_assignments", "worker_hostname", String()),
]


def ensure_schema(engine=None):
    """Add any columns the deployed database does not have yet.
    Raises on the first failure rather than continuing into a boot that cannot
    serve reads.
    """
    engine = engine or get_engine()

    with engine.connect() as conn:
        existing_tables = set(inspect(conn).get_table_names())

    for m in MIGRATIONS:
        if m.table not in existing_tables:
            continue
        try:
            with engine.begin() as conn:
                m.apply(conn)
        except Exception:
            logger.exception("Schema migration %s failed", m.name)
            raise

    verify_schema(engine)


class SchemaDriftError(RuntimeError):
    """The live database is missing something the models declare."""


def schema_drift(engine=None):
    """Columns and tables the models declare but the database does not have.

    Returns (missing_tables, missing_columns) as sorted lists of names.
    Columns the database has but the models no longer declare are ignored:
    that direction is normal during a rollback and breaks nothing.
    """
    engine = engine or get_engine()
    insp = inspect(engine)
    live_tables = set(insp.get_table_names())

    missing_tables, missing_columns = [], []
    for name, table in Base.metadata.tables.items():
        if name not in live_tables:
            missing_tables.append(name)
            continue
        live = {c["name"] for c in insp.get_columns(name)}
        missing_columns.extend(
            f"{name}.{col.name}" for col in table.columns if col.name not in live
        )
    return sorted(missing_tables), sorted(missing_columns)


def verify_schema(engine=None):
    """Fail the boot if the database is missing anything the models declare.

    MIGRATIONS is maintained by hand, so a model column shipped without an
    entry here is invisible in development — `create_all()` builds it into
    every fresh database — and missing only where the table predates the
    change, which in practice means production alone. The failure then
    surfaces as a 500 from whichever endpoint touches the column first, with
    nothing naming the real cause. `batch_assignments.org_id` /
    `.worker_hostname` stalled every batch in the queue that way.

    Raising here converts that into a deploy that refuses to start and says
    exactly which column is missing.
    """
    missing_tables, missing_columns = schema_drift(engine)
    if not missing_tables and not missing_columns:
        return

    detail = []
    if missing_tables:
        detail.append(f"tables: {', '.join(missing_tables)}")
    if missing_columns:
        detail.append(f"columns: {', '.join(missing_columns)}")
    message = (
        "Database schema is behind the models — " + "; ".join(detail) + ". "
        "Add a _Migration entry in backend/migrations.py for each missing "
        "column (tables come from Base.metadata.create_all)."
    )
    logger.error(message)
    raise SchemaDriftError(message)
