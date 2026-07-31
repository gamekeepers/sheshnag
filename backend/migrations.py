"""
Startup migrations for pre-existing SQLite databases.

`Base.metadata.create_all()` only creates missing tables — it never adds
columns to existing tables, backfills data, or drops anything. Everything
of that kind lives here and runs once at app startup, idempotently.

Current steps:
1. Add columns introduced after a table already shipped.
2. Backfill the normalized inventory tables (worker_runtimes,
   runtime_models, worker_gpus — spec §8.2–8.4) from the legacy JSON
   columns on workers, then drop those columns.
3. Drop the long-orphaned provider_capabilities table.
"""
import json
import logging
import uuid

from sqlalchemy import text

logger = logging.getLogger(__name__)

# Columns added after their table first shipped: table -> [(name, ddl)]
_NEW_COLUMNS = {
    "users": [
        ("google_id", "TEXT"),
        ("auth_provider", "TEXT DEFAULT 'local'"),
        ("platform_role", "TEXT DEFAULT 'user'"),
        ("is_active", "BOOLEAN DEFAULT 1"),
        ("must_change_password", "BOOLEAN DEFAULT 0"),
    ],
    "batches": [("attempts", "INTEGER DEFAULT 0"),("api_key_id", "TEXT"),],
    "workers": [
        ("activity", "TEXT DEFAULT 'idle'"),
        ("vram_total_gb", "REAL"),
        ("vram_available_gb", "REAL"),
        ("api_key_id", "TEXT"),
    ],
    "runtime_models": [("digest", "TEXT")],
    "model_catalog": [
        ("source_type", "TEXT"),
        ("source_ref", "TEXT"),
        ("source_revision", "TEXT"),
        ("homepage_url", "TEXT"),
        ("task_type", "TEXT"),
        ("parameter_size", "TEXT"),
        ("context_length", "INTEGER"),
    ],
    "api_keys": [
        ("key_type", "TEXT DEFAULT 'worker'"),
        ("created_by_user_id", "TEXT"),
        ("key_prefix", "TEXT"),
        ("key_hash", "TEXT"),
        ("status", "TEXT DEFAULT 'active'"),
        ("last_used_at", "INTEGER"),
        ("expires_at", "INTEGER"),
        ("revoked_at", "INTEGER"),
    ],
}

# Legacy JSON columns on workers, replaced by the normalized tables.
_LEGACY_WORKER_COLUMNS = ("gpus", "runtimes", "loaded_models")

# Descriptive columns pruned from runtime_models (moved to model_catalog).
# Dropped from pre-existing DBs; harmless if absent (fresh DB never had them).
_DROPPED_RUNTIME_MODEL_COLUMNS = (
    "revision", "task_type", "parameter_count", "quantization",
    "context_length", "size_bytes", "license", "local_path",
)


def _columns(conn, table: str) -> list:
    return [row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))]


# Indexes added after their table first shipped. ALTER TABLE ADD COLUMN
# cannot carry UNIQUE, so uniqueness on late columns lives here. Safe on
# existing DBs while the column is all-NULL (SQLite unique indexes allow
# multiple NULLs); a genuine duplicate fails loudly at startup, which is
# the correct outcome.
_NEW_INDEXES = [
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_google_id ON users(google_id)",
]


def _add_missing_indexes(conn) -> None:
    for ddl in _NEW_INDEXES:
        conn.execute(text(ddl))


def _normalize_user_emails(conn) -> None:
    """One-time lowercase/trim of users.email so lookups match the
    normalized form all auth endpoints now use. Accounts differing only
    by case are left untouched (merging them is a human decision)."""
    rows = conn.execute(text(
        "SELECT id, email FROM users WHERE email != lower(trim(email))"
    )).fetchall()
    for uid, email in rows:
        target = email.strip().lower()
        clash = conn.execute(
            text("SELECT id FROM users WHERE email = :e AND id != :id"),
            {"e": target, "id": uid},
        ).first()
        if clash:
            logger.warning(
                "users.email case-collision: %s (%s) vs %s — left as-is",
                uid, email, clash[0],
            )
            continue
        conn.execute(
            text("UPDATE users SET email = :e WHERE id = :id"),
            {"e": target, "id": uid},
        )


def _add_missing_columns(conn) -> None:
    for table, columns in _NEW_COLUMNS.items():
        existing = _columns(conn, table)
        if not existing:
            continue
        for name, ddl in columns:
            if name not in existing:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


def _backfill_inventory(conn) -> None:
    """Move workers.gpus/runtimes/loaded_models JSON into the normalized
    tables, then drop the JSON columns.

    Only runs when the legacy columns still exist. Raw SQL throughout —
    the ORM models no longer know about the old columns.
    """
    worker_cols = _columns(conn, "workers")
    legacy = [c for c in _LEGACY_WORKER_COLUMNS if c in worker_cols]
    if not legacy:
        return

    def new_id(prefix):
        return f"{prefix}-{uuid.uuid4().hex[:24]}"

    select_cols = ", ".join(["id"] + legacy)
    rows = conn.execute(text(f"SELECT {select_cols} FROM workers")).fetchall()
    migrated = 0
    for row in rows:
        data = dict(zip(["id"] + legacy, row))
        worker_id = data["id"]

        def parse(col):
            try:
                return json.loads(data.get(col) or "[]")
            except (json.JSONDecodeError, TypeError):
                return []

        loaded = set(parse("loaded_models"))

        for gpu in parse("gpus"):
            if not isinstance(gpu, dict):
                continue
            conn.execute(
                text(
                    "INSERT OR IGNORE INTO worker_gpus "
                    "(id, worker_id, gpu_index, vendor, name, vram_gb, driver, cuda, updated_at) "
                    "VALUES (:id, :wid, :idx, :vendor, :name, :vram, :driver, :cuda, unixepoch())"
                ),
                {
                    "id": new_id("gpu"), "wid": worker_id,
                    "idx": gpu.get("index", 0), "vendor": gpu.get("vendor"),
                    "name": gpu.get("name"), "vram": gpu.get("vram_gb"),
                    "driver": gpu.get("driver"), "cuda": gpu.get("cuda"),
                },
            )

        for rt in parse("runtimes"):
            if not isinstance(rt, dict):
                continue
            rt_id = new_id("wrt")
            conn.execute(
                text(
                    "INSERT OR IGNORE INTO worker_runtimes "
                    "(id, worker_id, engine, base_url, api_protocol, status, created_at, updated_at) "
                    "VALUES (:id, :wid, :engine, :url, 'openai-compatible', 'ready', unixepoch(), unixepoch())"
                ),
                {
                    "id": rt_id, "wid": worker_id,
                    "engine": rt.get("type", "ollama"),
                    "url": rt.get("endpoint", ""),
                },
            )
            for model in rt.get("models", []):
                if not isinstance(model, str):
                    continue
                conn.execute(
                    text(
                        "INSERT OR IGNORE INTO runtime_models "
                        "(id, runtime_id, name, runtime_model_id, status, loaded, created_at, updated_at) "
                        "VALUES (:id, :rt, :name, :name, 'available', :loaded, unixepoch(), unixepoch())"
                    ),
                    {
                        "id": new_id("rtm"), "rt": rt_id,
                        "name": model, "loaded": model in loaded,
                    },
                )
        migrated += 1

    for col in legacy:
        try:
            conn.execute(text(f"ALTER TABLE workers DROP COLUMN {col}"))
        except Exception:
            # SQLite < 3.35 can't drop columns — orphaned columns are harmless.
            logger.warning("Could not drop legacy column workers.%s", col)

    if migrated:
        logger.info("Backfilled inventory tables for %d workers", migrated)


def _drop_pruned_columns(conn) -> None:
    """Drop the descriptive columns pruned from runtime_models."""
    existing = _columns(conn, "runtime_models")
    if not existing:
        return
    for col in _DROPPED_RUNTIME_MODEL_COLUMNS:
        if col not in existing:
            continue
        try:
            conn.execute(text(f"ALTER TABLE runtime_models DROP COLUMN {col}"))
        except Exception:
            # SQLite < 3.35 can't drop columns — unused columns are harmless.
            logger.warning("Could not drop runtime_models.%s", col)

    # Drop legacy columns from users and organizations
    try:
        conn.execute(text("ALTER TABLE users DROP COLUMN role"))
    except Exception:
        pass
    try:
        conn.execute(text("ALTER TABLE organizations DROP COLUMN owner_id"))
    except Exception:
        pass
    try:
        conn.execute(text("ALTER TABLE api_keys DROP COLUMN key"))
    except Exception:
        pass


def run_startup_migrations(engine) -> None:
    """Run all idempotent schema/data migrations. Call after create_all."""
    with engine.connect() as conn:
        _add_missing_columns(conn)
        _add_missing_indexes(conn)
        _normalize_user_emails(conn)
        _backfill_inventory(conn)
        _drop_pruned_columns(conn)
        conn.execute(text("DROP TABLE IF EXISTS provider_capabilities"))
        conn.commit()
