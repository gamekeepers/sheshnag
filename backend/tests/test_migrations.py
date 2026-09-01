"""Forward-migration tests.

`Base.metadata.create_all()` creates missing tables but never adds columns to
tables that already exist, so a deployed database that predates the token
rollup columns would fail every Batch SELECT without these migrations.
"""

from sqlalchemy import inspect, text

from migrations import MIGRATIONS, ensure_schema

TOKEN_COLUMNS = ["prompt_tokens", "completion_tokens", "total_tokens"]


def _batch_columns(engine):
    with engine.connect() as conn:
        return {c["name"] for c in inspect(conn).get_columns("batches")}


def test_ensure_schema_adds_missing_columns_to_existing_table(_engine):
    """Simulates the deployed database: batches exists, the columns do not."""
    with _engine.begin() as conn:
        for col in TOKEN_COLUMNS:
            conn.execute(text(f"ALTER TABLE batches DROP COLUMN IF EXISTS {col}"))

    assert not (_batch_columns(_engine) & set(TOKEN_COLUMNS))

    ensure_schema(_engine)

    assert set(TOKEN_COLUMNS).issubset(_batch_columns(_engine))


def test_ensure_schema_is_idempotent(_engine):
    """Runs on every startup, so a second pass must be a no-op, not an error."""
    ensure_schema(_engine)
    before = _batch_columns(_engine)
    ensure_schema(_engine)
    assert _batch_columns(_engine) == before


def test_migrated_columns_are_writable(_engine):
    """An added column must be usable, not just present."""
    ensure_schema(_engine)
    with _engine.begin() as conn:
        conn.execute(text("UPDATE batches SET prompt_tokens = 1 WHERE 1 = 0"))


def test_every_migration_targets_a_real_model_column(_engine):
    """Guards against a migration drifting from the model it backfills."""
    ensure_schema(_engine)
    for m in MIGRATIONS:
        with _engine.connect() as conn:
            cols = {c["name"] for c in inspect(conn).get_columns(m.table)}
        assert m.column in cols, f"{m.name} did not produce {m.table}.{m.column}"
