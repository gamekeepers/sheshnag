"""Worker inventory reporting (#116): registration and heartbeats carry
on-disk artifact file hashes; availability rows track the disk."""

from models import RuntimeModel, WorkerRuntime


def _worker_key(auth_client, org_name):
    org = auth_client.post("/v1/me/organizations", json={"name": org_name}).json()
    key = auth_client.post(
        f"/v1/orgs/{org['id']}/api-keys", json={"name": "k", "key_type": "worker"}
    ).json()
    return key["api_key"]


def _rows(db_session, worker_id):
    return {
        m.name: m
        for rt in db_session.query(WorkerRuntime).filter_by(worker_id=worker_id)
        for m in db_session.query(RuntimeModel).filter_by(runtime_id=rt.id)
    }


def test_register_persists_inventory_hashes(auth_client, db_session):
    key = _worker_key(auth_client, "Inv Org One")
    payload = {
        "hostname": "inv-box",
        "runtimes": [{
            "type": "ollama", "endpoint": "localhost",
            "models": ["qwen3:4b"],
            "model_digests": {"qwen3:4b": "manifest-digest-legacy"},
            "inventory": [
                # File hash outranks the legacy manifest digest for the
                # same name; an on-disk model missing from `models` still
                # gets an availability row.
                {"local_name": "qwen3:4b", "sha256": "a" * 64,
                 "size_bytes": 2497280256, "loaded": False, "runtime": "ollama"},
                {"local_name": "gemma3:4b", "sha256": "b" * 64,
                 "size_bytes": 100, "loaded": False, "runtime": "ollama"},
            ],
        }],
    }
    resp = auth_client.post(
        "/workers/register", json=payload, headers={"Authorization": f"Bearer {key}"}
    )
    assert resp.status_code == 200, resp.text

    rows = _rows(db_session, resp.json()["worker_id"])
    assert rows["qwen3:4b"].digest == "a" * 64
    assert rows["gemma3:4b"].digest == "b" * 64


def test_register_without_inventory_still_works(auth_client, db_session):
    """Older daemons omit `inventory`; the field is additive — and their
    legacy `model_digests` (/api/tags MANIFEST digests) must NOT be stored
    as identity, or the picker's guard would reject every pinned model on
    a not-yet-upgraded daemon. They stay on name matching (digest null)."""
    key = _worker_key(auth_client, "Inv Org Legacy")
    payload = {
        "hostname": "old-daemon-box",
        "runtimes": [{
            "type": "ollama", "endpoint": "localhost",
            "models": ["qwen3:4b"],
            "model_digests": {"qwen3:4b": "legacy-digest"},
        }],
    }
    resp = auth_client.post(
        "/workers/register", json=payload, headers={"Authorization": f"Bearer {key}"}
    )
    assert resp.status_code == 200, resp.text
    rows = _rows(db_session, resp.json()["worker_id"])
    assert rows["qwen3:4b"].digest is None


def test_heartbeat_inventory_refreshes_digests_and_adds_rows(auth_client, db_session):
    key = _worker_key(auth_client, "Inv Org HB")
    resp = auth_client.post(
        "/workers/register",
        json={
            "hostname": "hb-box",
            "runtimes": [{
                "type": "ollama", "endpoint": "localhost", "models": ["qwen3:4b"],
            }],
        },
        headers={"Authorization": f"Bearer {key}"},
    )
    assert resp.status_code == 200, resp.text
    worker_id = resp.json()["worker_id"]

    hb = auth_client.post(
        f"/workers/{worker_id}/heartbeat",
        json={
            "worker_id": worker_id,
            "activity": "idle",
            "loaded_models": ["qwen3:4b"],
            "inventory": [
                # Known row gains its file hash…
                {"local_name": "qwen3:4b", "sha256": "a" * 64,
                 "size_bytes": 1, "loaded": True, "runtime": "ollama"},
                # …and a model pulled manually on the box (never registered,
                # not loaded — invisible to loaded_models) gets a row.
                {"local_name": "deepseek-r1:1.5b", "sha256": "d" * 64,
                 "size_bytes": 2, "loaded": False, "runtime": "ollama"},
            ],
        },
        headers={"Authorization": f"Bearer {key}"},
    )
    assert hb.status_code == 200, hb.text

    rows = _rows(db_session, worker_id)
    assert rows["qwen3:4b"].digest == "a" * 64
    assert rows["qwen3:4b"].loaded is True
    assert rows["deepseek-r1:1.5b"].digest == "d" * 64
    assert rows["deepseek-r1:1.5b"].loaded is False


def test_heartbeat_duplicate_inventory_name_does_not_500(auth_client, db_session):
    """A buggy daemon listing the same local_name twice in one report must
    not trip UniqueConstraint(runtime_id, name) at commit — that rolled back
    the whole heartbeat, liveness included, every 30s."""
    key = _worker_key(auth_client, "Inv Org Dup")
    resp = auth_client.post(
        "/workers/register",
        json={"hostname": "dup-box",
              "runtimes": [{"type": "ollama", "endpoint": "localhost", "models": []}]},
        headers={"Authorization": f"Bearer {key}"},
    )
    worker_id = resp.json()["worker_id"]
    hb = auth_client.post(
        f"/workers/{worker_id}/heartbeat",
        json={"worker_id": worker_id, "activity": "idle", "loaded_models": [],
              "inventory": [
                  {"local_name": "twice:1b", "sha256": "a" * 64, "loaded": False},
                  {"local_name": "twice:1b", "sha256": "a" * 64, "loaded": False},
              ]},
        headers={"Authorization": f"Bearer {key}"},
    )
    assert hb.status_code == 200, hb.text
    rows = _rows(db_session, worker_id)
    assert list(rows) == ["twice:1b"]


def test_digest_mismatch_rejects_and_warns_once(caplog):
    """A worker whose artifact differs from the catalogue pin is never
    scheduled — and the rejection is logged (once per distinct mismatch),
    so a silent starve is discoverable without reading the code."""
    import logging
    import provider_picker
    from provider_picker import _hosts

    provider_picker._warned_mismatches.clear()
    with caplog.at_level(logging.WARNING, logger="provider_picker"):
        assert not _hosts([("qwen3:4b", "a" * 64)], ["qwen3:4b"], "b" * 64)
        assert not _hosts([("qwen3:4b", "a" * 64)], ["qwen3:4b"], "b" * 64)
    mismatch_logs = [r for r in caplog.records if "Digest mismatch" in r.getMessage()]
    assert len(mismatch_logs) == 1
    assert "qwen3:4b" in mismatch_logs[0].getMessage()
    # Matching digests, or a missing one on either side, still schedule.
    assert _hosts([("qwen3:4b", "b" * 64)], ["qwen3:4b"], "b" * 64)
    assert _hosts([("qwen3:4b", None)], ["qwen3:4b"], "b" * 64)
