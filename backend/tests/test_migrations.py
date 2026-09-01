"""Forward-migration tests.

`Base.metadata.create_all()` creates missing tables but never adds columns to
tables that already exist, so a deployed database that predates the token
rollup columns would fail every Batch SELECT without these migrations.
"""

import pytest
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


def test_failed_migration_raises_instead_of_booting_broken(_engine, monkeypatch):
    """A swallowed failure boots a process that 500s on every Batch read.

    Startup must fail loudly instead — that is the failure an orchestrator can
    see and act on.
    """
    import migrations as migrations_module
    from sqlalchemy import Integer

    broken = migrations_module._Migration(
        "batches.broken", "batches", "not a valid column name", Integer()
    )
    monkeypatch.setattr(migrations_module, "MIGRATIONS", [broken])

    with pytest.raises(Exception):
        migrations_module.ensure_schema(_engine)

    assert "not a valid column name" not in _batch_columns(_engine)


# ── Drift guard ────────────────────────────────────────────────────────────
# MIGRATIONS is hand-maintained. A model column shipped without an entry is
# invisible in development (create_all builds it into every fresh database)
# and missing only where the table predates the change — in practice, only
# production. verify_schema turns that into a refused boot.

def test_verify_schema_passes_on_an_up_to_date_database(_engine):
    from migrations import verify_schema
    verify_schema(_engine)  # must not raise


def test_verify_schema_names_a_column_with_no_migration(_engine):
    """The prod outage: batch_assignments.worker_hostname had no entry."""
    from migrations import SchemaDriftError, schema_drift, verify_schema

    with _engine.begin() as conn:
        conn.execute(text(
            "ALTER TABLE batch_assignments DROP COLUMN IF EXISTS worker_hostname"))
    try:
        _tables, columns = schema_drift(_engine)
        assert "batch_assignments.worker_hostname" in columns

        with pytest.raises(SchemaDriftError) as exc:
            verify_schema(_engine)
        # The message must name the column, or it is no better than the 500.
        assert "batch_assignments.worker_hostname" in str(exc.value)
    finally:
        ensure_schema(_engine)

    assert schema_drift(_engine) == ([], [])


def test_ensure_schema_runs_the_drift_check(_engine):
    """A migration exists for it, so ensure_schema heals and does not raise."""
    with _engine.begin() as conn:
        conn.execute(text(
            "ALTER TABLE batch_assignments DROP COLUMN IF EXISTS org_id"))

    ensure_schema(_engine)

    with _engine.connect() as conn:
        cols = {c["name"] for c in inspect(conn).get_columns("batch_assignments")}
    assert "org_id" in cols


def test_drift_ignores_columns_the_models_no_longer_declare(_engine):
    """Extra columns are normal after a rollback and must not fail the boot."""
    from migrations import schema_drift, verify_schema

    with _engine.begin() as conn:
        conn.execute(text(
            "ALTER TABLE batch_assignments ADD COLUMN IF NOT EXISTS legacy_note VARCHAR"))
    try:
        assert schema_drift(_engine) == ([], [])
        verify_schema(_engine)  # must not raise
    finally:
        with _engine.begin() as conn:
            conn.execute(text(
                "ALTER TABLE batch_assignments DROP COLUMN IF EXISTS legacy_note"))


def test_ensure_schema_refuses_to_boot_when_a_migration_was_forgotten(
    _engine, monkeypatch
):
    """The exact prod outage, reproduced end to end.

    A model column shipped with no MIGRATIONS entry, on a table that predates
    it. Before the drift check, ensure_schema() completed happily and the app
    served traffic until the first INSERT touching the column returned 500.
    """
    import migrations
    from migrations import SchemaDriftError

    # No migration covers batch_assignments at all.
    monkeypatch.setattr(migrations, "MIGRATIONS", [])
    with _engine.begin() as conn:
        conn.execute(text(
            "ALTER TABLE batch_assignments DROP COLUMN IF EXISTS worker_hostname"))
    try:
        with pytest.raises(SchemaDriftError) as exc:
            migrations.ensure_schema(_engine)
        assert "batch_assignments.worker_hostname" in str(exc.value)
        assert "backend/migrations.py" in str(exc.value)
    finally:
        monkeypatch.undo()
        ensure_schema(_engine)
