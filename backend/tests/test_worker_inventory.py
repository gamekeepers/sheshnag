"""Worker inventory reporting (#116): registration and heartbeats carry
on-disk artifact file hashes; availability rows track the disk."""

from models import ModelCatalog, RuntimeModel, ServingProfile, Worker, WorkerRuntime
from scheduler import can_serve


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
    as identity, or the scheduler's guard would reject every pinned model on
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
    import scheduler
    from scheduler import _hosts

    scheduler._warned_mismatches.clear()
    with caplog.at_level(logging.WARNING, logger="scheduler"):
        assert not _hosts([("qwen3:4b", "a" * 64)], ["qwen3:4b"], "b" * 64)
        assert not _hosts([("qwen3:4b", "a" * 64)], ["qwen3:4b"], "b" * 64)
    mismatch_logs = [r for r in caplog.records if "Digest mismatch" in r.getMessage()]
    assert len(mismatch_logs) == 1
    assert "qwen3:4b" in mismatch_logs[0].getMessage()
    # Matching digests, or a missing one on either side, still schedule.
    assert _hosts([("qwen3:4b", "b" * 64)], ["qwen3:4b"], "b" * 64)
    assert _hosts([("qwen3:4b", None)], ["qwen3:4b"], "b" * 64)


# ── Multi-runtime workers (#134): one daemon, several runtimes ──────


def _rows_by_runtime(db_session, worker_id):
    """{engine: {model name: RuntimeModel}} — unlike _rows, keeps rows
    separate when the same name is hosted by two runtimes."""
    out = {}
    for rt in db_session.query(WorkerRuntime).filter_by(worker_id=worker_id):
        out[rt.engine] = {
            m.name: m
            for m in db_session.query(RuntimeModel).filter_by(runtime_id=rt.id)
        }
    return out


def _mixed_registration(key, hostname, runtimes):
    return {
        "hostname": hostname,
        "runtimes": [
            {
                "type": engine,
                "endpoint": "localhost",
                "models": models,
                "model_digests": {},
                "inventory": inventory,
            }
            for engine, models, inventory in runtimes
        ],
    }


def test_register_mixed_worker_creates_row_per_runtime(auth_client, db_session):
    """Each bundle in a multi-runtime registration becomes its own
    worker_runtimes row; the same model name on two runtimes yields two
    rows (one per runtime) with their own artifact hashes."""
    key = _worker_key(auth_client, "Multi Org Reg")
    payload = _mixed_registration(key, "mixed-box", [
        ("vllm", ["vllm-only:7b"], [
            {"local_name": "vllm-only:7b", "sha256": "c" * 64,
             "size_bytes": 1, "loaded": True, "runtime": "vllm"},
            {"local_name": "shared:1b", "sha256": "d" * 64,
             "size_bytes": 2, "loaded": False, "runtime": "vllm"},
        ]),
        ("ollama", ["ollama-only:8b"], [
            {"local_name": "ollama-only:8b", "sha256": "e" * 64,
             "size_bytes": 3, "loaded": True, "runtime": "ollama"},
            {"local_name": "shared:1b", "sha256": "f" * 64,
             "size_bytes": 4, "loaded": False, "runtime": "ollama"},
        ]),
    ])
    resp = auth_client.post(
        "/workers/register", json=payload, headers={"Authorization": f"Bearer {key}"}
    )
    assert resp.status_code == 200, resp.text
    worker_id = resp.json()["worker_id"]

    rows = _rows_by_runtime(db_session, worker_id)
    assert set(rows) == {"vllm", "ollama"}
    assert set(rows["vllm"]) == {"vllm-only:7b", "shared:1b"}
    assert set(rows["ollama"]) == {"ollama-only:8b", "shared:1b"}
    # Per-runtime artifact identity: the shared name keeps its own hash
    # under each runtime, and loaded state is per-runtime too.
    assert rows["vllm"]["vllm-only:7b"].digest == "c" * 64
    assert rows["vllm"]["vllm-only:7b"].loaded is True
    assert rows["vllm"]["shared:1b"].digest == "d" * 64
    assert rows["vllm"]["shared:1b"].loaded is False
    assert rows["ollama"]["ollama-only:8b"].digest == "e" * 64
    assert rows["ollama"]["shared:1b"].digest == "f" * 64


def test_heartbeat_routes_loaded_model_by_runtime_tag(auth_client, db_session):
    """A model the worker reports loaded but has no row for is filed under
    the runtime its inventory tag names — not runtimes[0]. Without the tag
    routing, the row would land under runtime #1 while reconciliation files
    a SECOND row under runtime #2: duplicates, one per beat."""
    key = _worker_key(auth_client, "Multi Org HB")
    resp = auth_client.post(
        "/workers/register",
        json=_mixed_registration(key, "mixed-hb-box", [
            ("vllm", ["vllm-only:7b"], []),
            ("ollama", ["ollama-only:8b"], []),
        ]),
        headers={"Authorization": f"Bearer {key}"},
    )
    assert resp.status_code == 200, resp.text
    worker_id = resp.json()["worker_id"]

    def beat():
        return auth_client.post(
            f"/workers/{worker_id}/heartbeat",
            json={
                "worker_id": worker_id,
                "activity": "idle",
                "loaded_models": ["ollama-only:8b", "fresh-pull:1b"],
                # A mixed node's beat carries the UNION inventory, tagged
                # per runtime (the daemon's _get_inventory). Sending only
                # one runtime's slice would mark the other's rows missing.
                "inventory": [
                    {"local_name": "vllm-only:7b", "size_bytes": 1,
                     "loaded": False, "runtime": "vllm"},
                    {"local_name": "ollama-only:8b", "size_bytes": 3,
                     "loaded": True, "runtime": "ollama"},
                    {"local_name": "fresh-pull:1b", "sha256": "g" * 64,
                     "size_bytes": 5, "loaded": True, "runtime": "ollama"},
                ],
            },
            headers={"Authorization": f"Bearer {key}"},
        )

    assert beat().status_code == 200
    rows = _rows_by_runtime(db_session, worker_id)
    assert "fresh-pull:1b" in rows["ollama"]
    assert "fresh-pull:1b" not in rows["vllm"]
    assert rows["ollama"]["fresh-pull:1b"].loaded is True
    assert rows["ollama"]["fresh-pull:1b"].digest == "g" * 64
    assert rows["ollama"]["ollama-only:8b"].loaded is True
    assert rows["vllm"]["vllm-only:7b"].loaded is False

    # A second identical beat must not duplicate the row.
    assert beat().status_code == 200
    rows = _rows_by_runtime(db_session, worker_id)
    total = sum(1 for engine_rows in rows.values() if "fresh-pull:1b" in engine_rows)
    assert total == 1


def test_heartbeat_untagged_loaded_model_falls_back_to_first_runtime(auth_client, db_session):
    """An untagged loaded model lands on the runtime the daemon listed first.

    Two workers register the same pair in opposite order. Each files the untagged
    model under its own first-registered runtime, so the target tracks the bundle
    order rather than the engine name or whatever order the database returns.
    """
    key = _worker_key(auth_client, "Multi Org Legacy")

    def _register(hostname, bundles):
        resp = auth_client.post(
            "/workers/register",
            json=_mixed_registration(key, hostname, bundles),
            headers={"Authorization": f"Bearer {key}"},
        )
        assert resp.status_code == 200, resp.text
        return resp.json()["worker_id"]

    def _beat(worker_id):
        hb = auth_client.post(
            f"/workers/{worker_id}/heartbeat",
            json={
                "worker_id": worker_id,
                "activity": "idle",
                "loaded_models": ["mystery:1b"],
                "inventory": [],
            },
            headers={"Authorization": f"Bearer {key}"},
        )
        assert hb.status_code == 200, hb.text

    vllm_bundle = ("vllm", ["vllm-only:7b"], [])
    ollama_bundle = ("ollama", ["ollama-only:8b"], [])
    workers = {
        "vllm": _register("vllm-first-box", [vllm_bundle, ollama_bundle]),
        "ollama": _register("ollama-first-box", [ollama_bundle, vllm_bundle]),
    }

    for first, worker_id in workers.items():
        for _ in range(2):          # repeated beats must not duplicate the row
            _beat(worker_id)
        db_session.expire_all()
        rows = _rows_by_runtime(db_session, worker_id)
        owning = [e for e, names in rows.items() if "mystery:1b" in names]
        assert owning == [first], f"{first}-first worker filed it under {owning}"
        assert rows[first]["mystery:1b"].loaded is True


def test_runtimes_are_ordered_by_position_not_insertion(auth_client, db_session):
    """`worker.runtimes[0]` follows `position`, not the order rows were inserted.

    Registration inserts in bundle order, so physical order and position agree and
    an unordered relationship would look correct. Swapping the two positions in
    place separates them: the row inserted second now holds position 0 and must
    load first.
    """
    key = _worker_key(auth_client, "Multi Org Ordering")
    resp = auth_client.post(
        "/workers/register",
        json=_mixed_registration(key, "ordering-box", [
            ("vllm", ["vllm-only:7b"], []),
            ("ollama", ["ollama-only:8b"], []),
        ]),
        headers={"Authorization": f"Bearer {key}"},
    )
    assert resp.status_code == 200, resp.text
    worker_id = resp.json()["worker_id"]

    runtimes = db_session.query(WorkerRuntime).filter_by(worker_id=worker_id).all()
    by_engine = {rt.engine: rt for rt in runtimes}
    assert by_engine["vllm"].position == 0      # bundle order, as registered
    assert by_engine["ollama"].position == 1

    by_engine["vllm"].position = 1
    by_engine["ollama"].position = 0
    db_session.commit()
    db_session.expire_all()

    worker = db_session.query(Worker).filter_by(id=worker_id).one()
    assert [rt.engine for rt in worker.runtimes] == ["ollama", "vllm"]
    assert worker.runtimes[0].position == 0



def test_register_persists_runtime_status_and_defaults_it(auth_client, db_session):
    """A runtime the daemon could not reach registers as `unavailable`.

    The row has to exist — replace-all would delete it and cascade away its
    models — so the daemon sends it with an empty catalogue and says why. A
    daemon predating the field omits it and means `ready`: it only ever
    registered runtimes it had reached.
    """
    key = _worker_key(auth_client, "Multi Org Status")
    payload = _mixed_registration(key, "status-box", [
        ("vllm", ["served:7b"], []),
        ("ollama", [], []),
    ])
    payload["runtimes"][1]["status"] = "unavailable"
    assert "status" not in payload["runtimes"][0], "vllm entry stays legacy-shaped"

    resp = auth_client.post("/workers/register", json=payload,
                            headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 200, resp.text
    worker_id = resp.json()["worker_id"]

    db_session.expire_all()
    rows = {rt.engine: rt for rt in
            db_session.query(WorkerRuntime).filter_by(worker_id=worker_id)}

    assert rows["ollama"].status == "unavailable"
    assert rows["vllm"].status == "ready", "omitted status means ready, not empty"


def test_unavailable_runtime_is_catalogued_but_not_dispatchable(auth_client, db_session):
    """A model on a down runtime stays in the catalogue and out of dispatch.

    The worker really does hold it, and that is worth knowing for capacity and
    provenance — the digests are expensive to recompute and survive the outage.
    What must not happen is the scheduler handing it a batch it cannot run.
    """
    key = _worker_key(auth_client, "Multi Org Dispatch")
    payload = _mixed_registration(key, "dispatch-box", [
        ("vllm", ["served:7b"], []),
        ("ollama", ["on-disk:8b"], []),
    ])
    payload["runtimes"][1]["status"] = "unavailable"

    resp = auth_client.post("/workers/register", json=payload,
                            headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 200, resp.text
    worker_id = resp.json()["worker_id"]

    db_session.expire_all()
    worker = db_session.query(Worker).filter_by(id=worker_id).one()

    names = worker.advertised_model_names()
    assert "on-disk:8b" in names, "the catalogue keeps what the worker holds"
    assert "served:7b" in names

    dispatchable = {n for n, _digest in worker.advertised_models()}
    assert "on-disk:8b" not in dispatchable, "a down runtime must not be given work"
    assert "served:7b" in dispatchable


def test_heartbeat_loaded_is_scoped_per_runtime(auth_client, db_session):
    """`loaded_models` carries no runtime tag, so on a mixed worker it can
    only be applied as a union. The tagged inventory re-scopes it: a model
    resident on ollama must not light up the vllm row for the same name."""
    key = _worker_key(auth_client, "Loaded Scope Org")
    payload = _mixed_registration(key, "scope-box", [
        ("vllm", ["shared:1b"], [
            {"local_name": "shared:1b", "sha256": "a" * 64,
             "size_bytes": 1, "loaded": False, "runtime": "vllm"},
        ]),
        ("ollama", ["shared:1b"], [
            {"local_name": "shared:1b", "sha256": "b" * 64,
             "size_bytes": 2, "loaded": False, "runtime": "ollama"},
        ]),
    ])
    resp = auth_client.post(
        "/workers/register", json=payload, headers={"Authorization": f"Bearer {key}"}
    )
    assert resp.status_code == 200, resp.text
    worker_id = resp.json()["worker_id"]

    hb = auth_client.post(
        f"/workers/{worker_id}/heartbeat",
        json={
            "worker_id": worker_id,
            "activity": "idle",
            "loaded_models": ["shared:1b"],       # union: resident *somewhere*
            "inventory": [
                {"local_name": "shared:1b", "sha256": "a" * 64,
                 "size_bytes": 1, "loaded": False, "runtime": "vllm"},
                {"local_name": "shared:1b", "sha256": "b" * 64,
                 "size_bytes": 2, "loaded": True, "runtime": "ollama"},
            ],
        },
        headers={"Authorization": f"Bearer {key}"},
    )
    assert hb.status_code == 200, hb.text

    rows = _rows_by_runtime(db_session, worker_id)
    assert rows["ollama"]["shared:1b"].loaded is True
    assert rows["vllm"]["shared:1b"].loaded is False


def test_heartbeat_inventory_outranks_the_union_flag(auth_client, db_session):
    """A model unloaded from VRAM but still on disk goes back to loaded=False
    on an EXISTING row, even while the untagged `loaded_models` union still
    names it. The tagged inventory is the authority; the union is what the
    backend falls back to for daemons that send no inventory at all."""
    key = _worker_key(auth_client, "Evict Org")
    resp = auth_client.post(
        "/workers/register",
        json={
            "hostname": "evict-box",
            "runtimes": [{
                "type": "ollama", "endpoint": "localhost", "models": ["hot:1b"],
                "inventory": [{"local_name": "hot:1b", "sha256": "e" * 64,
                               "size_bytes": 1, "loaded": True, "runtime": "ollama"}],
            }],
        },
        headers={"Authorization": f"Bearer {key}"},
    )
    assert resp.status_code == 200, resp.text
    worker_id = resp.json()["worker_id"]
    assert _rows(db_session, worker_id)["hot:1b"].loaded is True

    hb = auth_client.post(
        f"/workers/{worker_id}/heartbeat",
        json={
            "worker_id": worker_id,
            "activity": "idle",
            "loaded_models": ["hot:1b"],          # stale union still claims it
            "inventory": [{"local_name": "hot:1b", "sha256": "e" * 64,
                           "size_bytes": 1, "loaded": False, "runtime": "ollama"}],
        },
        headers={"Authorization": f"Bearer {key}"},
    )
    assert hb.status_code == 200, hb.text

    row = _rows(db_session, worker_id)["hot:1b"]
    assert row.loaded is False
    assert row.status != "missing"      # still on disk, just not resident


# ── llama.cpp: the day-one path ──────────────────────────────────
#
# `LlamaCppExecutor.inventory()` reports `sha256: None` deliberately. These
# pin both halves of that decision: the null-hash row stays schedulable, and
# a hash no catalogue entry carries does not.

def _llamacpp_registration(hostname, sha256):
    return {
        "hostname": hostname,
        "gpus": [{"index": 0, "vendor": "nvidia", "name": "GTX 750 Ti", "vram_gb": 2.0}],
        "ram": {"total_gb": 128.0},
        "runtimes": [{
            "type": "llamacpp", "endpoint": "localhost",
            "models": [],
            "model_digests": {},
            "inventory": [{
                "local_name": "qwen36-27b-q4kxl", "sha256": sha256,
                "size_bytes": 17601570816, "loaded": True, "runtime": "llamacpp",
            }],
        }],
    }


def test_llamacpp_registers_as_a_schedulable_runtime(auth_client, db_session):
    key = _worker_key(auth_client, "Llamacpp Org One")
    resp = auth_client.post(
        "/workers/register", json=_llamacpp_registration("gguf-box", None),
        headers={"Authorization": f"Bearer {key}"},
    )
    assert resp.status_code == 200, resp.text
    worker_id = resp.json()["worker_id"]

    runtimes = db_session.query(WorkerRuntime).filter_by(worker_id=worker_id).all()
    assert [rt.engine for rt in runtimes] == ["llamacpp"]
    assert runtimes[0].schedulable

    row = _rows(db_session, worker_id)["qwen36-27b-q4kxl"]
    assert row.status == "available"      # no hash ⇒ name matching, never quarantine
    assert row.schedulable
    assert row.digest is None
    assert row.loaded is True             # llama-server holds it for its lifetime


def test_a_hash_the_catalogue_cannot_place_strands_the_worker(auth_client, db_session):
    """Why `inventory()` reports no hash. Either way the row stops being
    schedulable while the worker still registers and still looks healthy —
    which is the failure mode worth avoiding, not the label on it.

    Both branches are reached by the same reported hash; only the name
    differs, because the catalogue pins one of the two.
    """
    key = _worker_key(auth_client, "Llamacpp Org Two")

    # A name the catalogue pins, reported with a different artifact: drift.
    # The reproducibility guard — same tag, different bytes.
    drifted = auth_client.post(
        "/workers/register", json=_llamacpp_registration("hashed-box", "f" * 64),
        headers={"Authorization": f"Bearer {key}"},
    )
    assert drifted.status_code == 200, drifted.text
    row = _rows(db_session, drifted.json()["worker_id"])["qwen36-27b-q4kxl"]
    assert row.status == "drift"
    assert not row.schedulable

    # A name the catalogue has never heard of: unregistered.
    payload = _llamacpp_registration("hashed-box-2", "f" * 64)
    payload["runtimes"][0]["inventory"][0]["local_name"] = "some-unseeded.gguf"
    unknown = auth_client.post(
        "/workers/register", json=payload,
        headers={"Authorization": f"Bearer {key}"},
    )
    assert unknown.status_code == 200, unknown.text
    row = _rows(db_session, unknown.json()["worker_id"])["some-unseeded.gguf"]
    assert row.status == "unregistered"
    assert not row.schedulable


def test_registered_llamacpp_worker_is_served_a_model_larger_than_its_card(
    auth_client, db_session,
):
    """The whole chain on real rows: a 2 GB card plus system RAM is offered a
    25 GB model, and the identical hardware on Ollama is not."""
    entry = ModelCatalog(
        id="zztest-gguf-27b-q4kxl", display_name="gguf 27b",
        runtime="llamacpp", runtime_model_id="zztest-gguf-27b",
        quantization="Q4_K_XL", vram_gb=25.0, size_gb=16.4,
    )
    db_session.add(entry)
    db_session.flush()
    db_session.add(ServingProfile(
        catalog_id=entry.id, runtime="llamacpp",
        runtime_model_id="zztest-gguf-27b",
    ))
    db_session.commit()

    try:
        key = _worker_key(auth_client, "Llamacpp Org Three")
        payload = _llamacpp_registration("hybrid-box", None)
        payload["runtimes"][0]["inventory"][0]["local_name"] = "zztest-gguf-27b"
        worker_id = auth_client.post(
            "/workers/register", json=payload,
            headers={"Authorization": f"Bearer {key}"},
        ).json()["worker_id"]

        # Free RAM arrives on the heartbeat, not at registration, and the
        # hybrid rule counts nothing without it.
        hb = auth_client.post(
            f"/workers/{worker_id}/heartbeat",
            json={"worker_id": worker_id, "activity": "idle",
                  "vram_total_gb": 2.0, "ram_available_gb": 126.0},
            headers={"Authorization": f"Bearer {key}"},
        )
        assert hb.status_code == 200, hb.text

        db_session.expire_all()
        worker = db_session.query(Worker).filter_by(id=worker_id).one()
        assert can_serve(entry, worker) is True

        # Same rows, same hardware, different engine: Ollama fits on VRAM
        # alone, so 2 GB cannot take a 25 GB model.
        worker.runtimes[0].engine = "ollama"
        db_session.flush()
        assert can_serve(entry, worker) is False
    finally:
        db_session.rollback()
        db_session.query(ServingProfile).filter_by(catalog_id=entry.id).delete()
        db_session.query(ModelCatalog).filter_by(id=entry.id).delete()
        db_session.commit()
