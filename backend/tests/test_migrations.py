"""Tests for the raw-SQL startup migrations (backend/migrations.py).

The rest of the suite runs against the current schema, so nothing else
exercises the legacy backfill path. These tests rebuild a pre-normalization
database (workers still carrying the JSON inventory columns) and assert the
migration moves the data into the normalized tables correctly.
"""
import json

import pytest
from sqlalchemy import StaticPool, create_engine, text

# Import models to register their tables with Base.metadata.
import models  # noqa: F401
from database import Base
from migrations import run_startup_migrations


@pytest.fixture
def legacy_engine():
    """Isolated in-memory DB shaped like a pre-normalization database.

    StaticPool pins one connection so every engine.connect() call (including
    the one inside run_startup_migrations) sees the same in-memory DB. Kept
    separate from the session-scoped shared engine so the schema mutation
    below can't leak into other tests.
    """
    eng = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(eng)

    with eng.connect() as conn:
        # Re-add the legacy JSON columns the migration expects to find.
        for col in ("gpus", "runtimes", "loaded_models"):
            conn.execute(text(f"ALTER TABLE workers ADD COLUMN {col} TEXT"))
        conn.execute(text(
            "INSERT INTO organizations (id, name) VALUES ('org-legacy', 'Legacy Org')"
        ))
        conn.execute(text(
            "INSERT INTO workers (id, org_id, hostname) "
            "VALUES ('wk-legacy', 'org-legacy', 'legacy-host')"
        ))
        conn.execute(text(
            "UPDATE workers SET gpus = :gpus, runtimes = :runtimes, "
            "loaded_models = :loaded WHERE id = 'wk-legacy'"
        ), {
            "gpus": json.dumps([
                {"index": 0, "vendor": "nvidia", "name": "H100",
                 "vram_gb": 80, "driver": "550.54.15", "cuda": "12.4"},
            ]),
            "runtimes": json.dumps([
                {"type": "ollama", "endpoint": "http://10.0.0.5:11434",
                 "models": ["llama3:8b", "mistral:7b"]},
            ]),
            "loaded": json.dumps(["llama3:8b"]),
        })
        conn.commit()

    yield eng
    eng.dispose()


def test_backfill_moves_legacy_json_into_normalized_tables(legacy_engine):
    # The exact path that crashed on SQLite 3.37 (unixepoch()).
    run_startup_migrations(legacy_engine)

    with legacy_engine.connect() as conn:
        gpus = conn.execute(text(
            "SELECT gpu_index, vendor, name, vram_gb, driver, cuda "
            "FROM worker_gpus WHERE worker_id = 'wk-legacy'"
        )).fetchall()
        assert gpus == [(0, "nvidia", "H100", 80.0, "550.54.15", "12.4")]

        runtimes = conn.execute(text(
            "SELECT engine, base_url, api_protocol, status "
            "FROM worker_runtimes WHERE worker_id = 'wk-legacy'"
        )).fetchall()
        assert runtimes == [("ollama", "http://10.0.0.5:11434",
                             "openai-compatible", "ready")]

        model_rows = conn.execute(text(
            "SELECT name, runtime_model_id, status, loaded FROM runtime_models "
            "WHERE runtime_id = (SELECT id FROM worker_runtimes "
            "WHERE worker_id = 'wk-legacy')"
        )).fetchall()
        assert sorted(model_rows) == [
            ("llama3:8b", "llama3:8b", "available", 1),   # was in loaded_models
            ("mistral:7b", "mistral:7b", "available", 0),
        ]

        # Backfilled timestamps must land as integers, not text.
        ts_types = {t for (t,) in conn.execute(text(
            "SELECT DISTINCT typeof(created_at) || '/' || typeof(updated_at) "
            "FROM worker_runtimes"
        ))}
        assert ts_types == {"integer/integer"}

        # Legacy JSON columns are dropped after a successful backfill.
        cols = {r[1] for r in conn.execute(text("PRAGMA table_info(workers)"))}
        assert not cols & {"gpus", "runtimes", "loaded_models"}


def test_backfill_is_noop_on_fresh_schema(legacy_engine):
    # Second run against an already-migrated DB must not fail or duplicate.
    run_startup_migrations(legacy_engine)
    run_startup_migrations(legacy_engine)

    with legacy_engine.connect() as conn:
        assert conn.execute(text(
            "SELECT COUNT(*) FROM worker_runtimes"
        )).scalar() == 1
