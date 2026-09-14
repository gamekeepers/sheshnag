"""POST /workers/poll — the only transition out of "validated".

No other code path moves a batch forward, so anything that makes this
endpoint raise stops the whole queue: the transaction rolls back, the batch
reverts to "validated", and the dashboard still shows workers healthy and
idle. A schema older than the model is one way to get there.
"""

from models import (
    Batch, BatchAssignment, ModelCatalog, RuntimeModel,
    Worker, WorkerRuntime, unix_now,
)


def _worker_key(auth_client):
    org = auth_client.post("/v1/me/organizations", json={"name": "Poll Org"}).json()
    key = auth_client.post(
        f"/v1/orgs/{org['id']}/api-keys", json={"name": "k", "key_type": "worker"}
    ).json()
    return key["api_key"]


def _register(auth_client, key, model="poll-model:latest"):
    resp = auth_client.post(
        "/workers/register",
        json={
            "hostname": "poll-box",
            "gpus": [{"index": 0, "name": "RTX 4090", "vram_gb": 24.0}],
            "runtimes": [{"type": "ollama", "endpoint": "localhost", "models": [model]}],
        },
        headers={"Authorization": f"Bearer {key}"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["worker_id"]


def _catalog(db, entry_id="poll-model", vram_gb=8.0):
    entry = db.query(ModelCatalog).filter_by(id=entry_id).first()
    if entry is None:
        entry = ModelCatalog(
            id=entry_id, display_name="Poll Model", runtime="ollama",
            runtime_model_id=f"{entry_id}:latest", vram_gb=vram_gb,
            enabled=True, status="active",
        )
        db.add(entry)
        db.commit()
    return entry


def _batch(db, batch_id, model="poll-model", created_at=1):
    db.add(Batch(id=batch_id, endpoint="/v1/chat/completions",
                 input_file_id="f-poll", status="validated",
                 model=model, created_at=created_at))
    db.commit()


def _poll(auth_client, key, worker_id):
    return auth_client.post(
        "/workers/poll", json={"worker_id": worker_id},
        headers={"Authorization": f"Bearer {key}"},
    )


def _reset(db):
    db.query(BatchAssignment).delete()
    db.query(Batch).delete()
    db.query(RuntimeModel).delete()
    db.query(WorkerRuntime).delete()
    db.query(Worker).delete()
    db.commit()


def test_poll_dispatches_the_oldest_servable_batch(auth_client, db_session):
    """The ordinary path: validated -> in_progress, with an assignment."""
    _reset(db_session)
    key = _worker_key(auth_client)
    worker_id = _register(auth_client, key)
    _catalog(db_session)
    _batch(db_session, "batch-clean", created_at=1)

    resp = _poll(auth_client, key, worker_id)

    assert resp.status_code == 200, resp.text
    assert resp.json()["job"]["job_id"] == "batch-clean"
    db_session.expire_all()
    assert db_session.get(Batch, "batch-clean").status == "in_progress"
    _reset(db_session)


def test_poll_works_on_a_database_created_before_the_snapshot_columns(
    auth_client, db_session
):
    """Prod's failure: batch_assignments predates org_id/worker_hostname.

    create_all() never adds columns to an existing table, so a database
    older than those model fields keeps the 3-column table. Every poll then
    INSERTs columns that do not exist -> UndefinedColumn -> 500 -> rollback,
    and the batch drops back to "validated". Poll is the only transition out
    of that state, so the entire queue stops with nothing in the UI to show
    why. ensure_schema() must close the gap at startup.
    """
    from sqlalchemy import inspect, text
    from migrations import ensure_schema

    _reset(db_session)
    bind = db_session.get_bind()

    # Reproduce the old schema exactly as found on prod.
    db_session.execute(text("ALTER TABLE batch_assignments DROP COLUMN IF EXISTS org_id"))
    db_session.execute(text("ALTER TABLE batch_assignments DROP COLUMN IF EXISTS worker_hostname"))
    db_session.commit()
    cols = {c["name"] for c in inspect(bind).get_columns("batch_assignments")}
    assert cols == {"batch_id", "worker_id", "assigned_at"}, cols

    ensure_schema(bind.engine if hasattr(bind, "engine") else bind)

    cols = {c["name"] for c in inspect(bind).get_columns("batch_assignments")}
    assert {"org_id", "worker_hostname"} <= cols, cols

    # And the queue actually moves again.
    key = _worker_key(auth_client)
    worker_id = _register(auth_client, key)
    _catalog(db_session)
    _batch(db_session, "batch-migrated", created_at=1)

    resp = _poll(auth_client, key, worker_id)
    assert resp.status_code == 200, resp.text
    assert resp.json()["job"]["job_id"] == "batch-migrated"
    _reset(db_session)
