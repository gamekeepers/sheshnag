"""Worker registration persists per-GPU vendor and ROCm/CUDA versions (spec §8.4)."""

from models import WorkerGpu


def _worker_key(auth_client):
    org = auth_client.post("/v1/me/organizations", json={"name": "AMD Org"}).json()
    key = auth_client.post(
        f"/v1/orgs/{org['id']}/api-keys", json={"name": "k", "key_type": "worker"}
    ).json()
    return key["api_key"]


def test_register_persists_amd_vendor_and_rocm(auth_client, db_session):
    key = _worker_key(auth_client)
    payload = {
        "hostname": "amd-box",
        "os": "Linux",
        "cpu": {"cores": 16},
        "ram": {"total_gb": 64.0},
        "gpus": [
            {"index": 0, "vendor": "amd", "name": "Radeon RX 6700 XT",
             "vram_gb": 9.98, "driver": "6.12.12", "cuda": None, "rocm": "6.4.0"},
            {"index": 1, "vendor": "nvidia", "name": "RTX 4090",
             "vram_gb": 23.99, "driver": "535.183.01", "cuda": "12.2", "rocm": None},
        ],
        "runtimes": [{"type": "ollama", "endpoint": "localhost", "models": []}],
    }
    resp = auth_client.post(
        "/workers/register", json=payload, headers={"Authorization": f"Bearer {key}"}
    )
    assert resp.status_code == 200, resp.text
    worker_id = resp.json()["worker_id"]

    rows = {g.gpu_index: g for g in db_session.query(WorkerGpu).filter_by(worker_id=worker_id)}
    assert rows[0].vendor == "amd"
    assert rows[0].rocm == "6.4.0"
    assert rows[0].cuda is None
    assert rows[1].vendor == "nvidia"
    assert rows[1].cuda == "12.2"
    assert rows[1].rocm is None


def test_register_without_rocm_field_still_works(auth_client, db_session):
    """Older daemons omit `rocm`; the field is additive."""
    key = _worker_key(auth_client)
    payload = {
        "hostname": "old-daemon",
        "gpus": [{"index": 0, "name": "RTX 3090", "vram_gb": 24.0, "cuda": "12.1"}],
    }
    resp = auth_client.post(
        "/workers/register", json=payload, headers={"Authorization": f"Bearer {key}"}
    )
    assert resp.status_code == 200, resp.text
    gpu = db_session.query(WorkerGpu).filter_by(worker_id=resp.json()["worker_id"]).one()
    assert gpu.vendor == "nvidia" and gpu.rocm is None
