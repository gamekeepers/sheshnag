"""Hardware detection — Apple Silicon support and the non-NVIDIA fallback.

Everything here mocks subprocess and the optional Metal import, so the suite
runs identically on a Linux CI box and on a Mac.
"""
import logging

import pytest

from daemon import hardware
from daemon.heartbeat import HeartbeatManager


@pytest.fixture(autouse=True)
def _fresh_vram_cache():
    """_apple_vram_gb is memoised per process; tests must not share it."""
    hardware._apple_vram_gb.cache_clear()
    yield
    hardware._apple_vram_gb.cache_clear()


# ─── Apple VRAM precedence ──────────────────────────────────

def test_vram_prefers_metal(monkeypatch):
    """Metal's own figure wins — it already reflects any wired-limit override."""
    monkeypatch.setattr(hardware, "_metal_vram_gb", lambda: 17.76)
    monkeypatch.setattr(hardware, "_sysctl", lambda name: pytest.fail(
        f"sysctl({name}) called even though Metal answered"
    ))

    assert hardware._apple_vram_gb() == 17.76


def test_vram_falls_back_to_wired_limit(monkeypatch):
    """Without Metal, an explicitly configured cap is authoritative."""
    monkeypatch.setattr(hardware, "_metal_vram_gb", lambda: None)
    monkeypatch.setattr(hardware, "_sysctl", lambda name: {
        "iogpu.wired_limit_mb": "20480",          # 20 GiB
        "hw.memsize": str(24 * 1024 ** 3),
    }.get(name))

    assert hardware._apple_vram_gb() == 20.0


def test_vram_falls_back_to_ratio(monkeypatch):
    """No Metal and no explicit cap: a conservative slice of total RAM."""
    monkeypatch.setattr(hardware, "_metal_vram_gb", lambda: None)
    monkeypatch.setattr(hardware, "_sysctl", lambda name: {
        "iogpu.wired_limit_mb": "0",              # 0 means "macOS default"
        "hw.memsize": str(24 * 1024 ** 3),
    }.get(name))

    assert hardware._apple_vram_gb() == pytest.approx(24 * 0.66, abs=0.01)


def test_vram_unknown_when_nothing_answers(monkeypatch):
    monkeypatch.setattr(hardware, "_metal_vram_gb", lambda: None)
    monkeypatch.setattr(hardware, "_sysctl", lambda name: None)

    assert hardware._apple_vram_gb() == 0.0


def test_metal_query_survives_missing_module(monkeypatch):
    """pyobjc absent (any non-macOS worker) must degrade, not raise."""
    import builtins
    real_import = builtins.__import__

    def no_metal(name, *args, **kwargs):
        if name == "Metal":
            raise ImportError("No module named 'Metal'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_metal)
    assert hardware._metal_vram_gb() is None


# ─── Registration entry ─────────────────────────────────────

_SP_JSON = """{"SPDisplaysDataType": [{
    "_name": "Apple M5",
    "sppci_model": "Apple M5",
    "sppci_bus": "spdisplays_builtin",
    "sppci_cores": "10",
    "spdisplays_mtlgpufamilysupport": "spdisplays_metal4"
}]}"""


def test_apple_gpu_entry(monkeypatch):
    monkeypatch.setattr(hardware, "_apple_vram_gb", lambda: 17.76)
    monkeypatch.setattr(
        hardware.subprocess, "run",
        lambda *a, **kw: type("R", (), {"stdout": _SP_JSON})(),
    )

    gpu = hardware._apple_gpu()

    assert gpu["name"] == "Apple M5 (10-core GPU)"
    assert gpu["vram_gb"] == 17.76
    assert gpu["driver_version"] == "Metal 4"
    assert gpu["cuda_version"] == ""      # Metal, not CUDA
    assert gpu["vendor"] == "apple"


_SP_JSON_ODD_SHAPES = """{"SPDisplaysDataType": [
    "not-a-dict",
    {"_name": "Apple M5", "sppci_bus": "spdisplays_builtin",
     "spdisplays_mtlgpufamilysupport": ["spdisplays_metal4"]}
]}"""


def test_apple_gpu_survives_unexpected_system_profiler_shapes(monkeypatch):
    """Lists where strings were expected, non-dict entries: degrade, don't raise."""
    monkeypatch.setattr(hardware, "_apple_vram_gb", lambda: 17.76)
    monkeypatch.setattr(
        hardware.subprocess, "run",
        lambda *a, **kw: type("R", (), {"stdout": _SP_JSON_ODD_SHAPES})(),
    )

    gpu = hardware._apple_gpu()
    assert gpu is not None and gpu["vram_gb"] == 17.76


def test_apple_gpu_omitted_when_vram_unknown(monkeypatch):
    """Advertise nothing rather than a GPU with no capacity."""
    monkeypatch.setattr(hardware, "_apple_vram_gb", lambda: 0.0)
    monkeypatch.setattr(
        hardware.subprocess, "run",
        lambda *a, **kw: type("R", (), {"stdout": _SP_JSON})(),
    )

    assert hardware._apple_gpu() is None


def test_apple_gpu_survives_system_profiler_failure(monkeypatch):
    """Identity is best-effort; the capacity number is what matters."""
    monkeypatch.setattr(hardware, "_apple_vram_gb", lambda: 17.76)

    def boom(*a, **kw):
        raise OSError("system_profiler missing")

    monkeypatch.setattr(hardware.subprocess, "run", boom)

    gpu = hardware._apple_gpu()
    assert gpu["name"] == "Apple GPU"
    assert gpu["vram_gb"] == 17.76


# ─── get_gpu_utilization dispatch ───────────────────────────

def test_utilization_on_darwin(monkeypatch):
    monkeypatch.setattr(hardware.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(hardware.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(hardware, "_apple_vram_gb", lambda: 17.76)
    monkeypatch.setattr(hardware.shutil, "which", lambda _: pytest.fail(
        "looked for nvidia-smi on macOS"
    ))

    stats = hardware.get_gpu_utilization()

    assert stats["memory_total_gb"] == 17.76
    # No machine-wide "in use" counter exists on unified memory → unknown.
    assert stats["memory_used_gb"] is None


def test_utilization_on_intel_mac_does_not_use_unified_memory_heuristic(monkeypatch):
    """An Intel Mac has a real (small) iGPU; 0.66 × RAM would be a lie."""
    monkeypatch.setattr(hardware.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(hardware.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(hardware, "_apple_vram_gb", lambda: pytest.fail(
        "unified-memory ceiling consulted on an Intel Mac"
    ))
    monkeypatch.setattr(hardware.shutil, "which", lambda _: None)

    assert hardware.get_gpu_utilization()["memory_total_gb"] == 0.0


def test_utilization_without_nvidia_smi_unchanged(monkeypatch):
    """The pre-existing behaviour on a Linux host with no NVIDIA driver."""
    monkeypatch.setattr(hardware.platform, "system", lambda: "Linux")
    monkeypatch.setattr(hardware.shutil, "which", lambda _: None)

    assert hardware.get_gpu_utilization() == {
        "utilization": 0.0, "memory_used_gb": 0.0, "memory_total_gb": 0.0,
    }


def test_utilization_with_nvidia_smi_unchanged(monkeypatch):
    """Regression guard: the NVIDIA path must be untouched by this change."""
    monkeypatch.setattr(hardware.platform, "system", lambda: "Linux")
    monkeypatch.setattr(hardware.shutil, "which", lambda _: "/usr/bin/nvidia-smi")
    monkeypatch.setattr(hardware, "_run_smi", lambda *a, **kw: "37, 3421, 24564\n")

    stats = hardware.get_gpu_utilization()

    assert stats["utilization"] == 37.0
    assert stats["memory_used_gb"] == pytest.approx(3.34, abs=0.01)
    assert stats["memory_total_gb"] == pytest.approx(23.99, abs=0.01)


# ─── Heartbeat fallback ─────────────────────────────────────

@pytest.mark.asyncio
async def test_declared_vram_used_when_probe_returns_zero(monkeypatch):
    """The general non-NVIDIA rescue: AMD, Intel, CPU-only, older daemons."""
    monkeypatch.setattr(
        "daemon.heartbeat.get_gpu_utilization",
        lambda: {"utilization": 0.0, "memory_used_gb": 0.0, "memory_total_gb": 0.0},
    )
    manager = HeartbeatManager(client=None, worker_id="w1", declared_vram_gb=16.0)

    payload = await manager._build_payload()

    assert payload["vram_total_gb"] == 16.0
    assert payload["vram_available_gb"] == 16.0


@pytest.mark.asyncio
async def test_declared_vram_overrides_probe(monkeypatch):
    """A provider lending less than the card holds must be respected."""
    monkeypatch.setattr(
        "daemon.heartbeat.get_gpu_utilization",
        lambda: {"utilization": 0.0, "memory_used_gb": 0.0, "memory_total_gb": 24.0},
    )
    manager = HeartbeatManager(client=None, worker_id="w1", declared_vram_gb=12.0)

    assert (await manager._build_payload())["vram_total_gb"] == 12.0


@pytest.mark.asyncio
async def test_probe_used_when_nothing_declared(monkeypatch):
    monkeypatch.setattr(
        "daemon.heartbeat.get_gpu_utilization",
        lambda: {"utilization": 0.0, "memory_used_gb": 4.0, "memory_total_gb": 24.0},
    )
    manager = HeartbeatManager(client=None, worker_id="w1")

    payload = await manager._build_payload()

    assert payload["vram_total_gb"] == 24.0
    assert payload["vram_available_gb"] == 20.0


# ─── Ceiling is computed once ───────────────────────────────

def test_apple_vram_computed_once(monkeypatch):
    calls = []
    monkeypatch.setattr(hardware, "_metal_vram_gb", lambda: calls.append(1) or 17.76)

    assert hardware._apple_vram_gb() == 17.76
    monkeypatch.setattr(hardware, "_metal_vram_gb", lambda: None)  # "hiccup"
    assert hardware._apple_vram_gb() == 17.76                     # source does not flap
    assert len(calls) == 1


# ─── detect_hardware gating ─────────────────────────────────

def _darwin(monkeypatch, machine):
    monkeypatch.setattr(hardware.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(hardware.platform, "machine", lambda: machine)
    monkeypatch.setattr(hardware, "_sysctl", lambda name: {
        "machdep.cpu.brand_string": "Apple M5", "hw.memsize": str(24 * 1024 ** 3),
    }.get(name))


def test_detect_on_apple_silicon_skips_nvidia_smi(monkeypatch):
    """No fall-through: a second index-0 entry would violate the DB unique key."""
    _darwin(monkeypatch, "arm64")
    monkeypatch.setattr(hardware, "_apple_gpu", lambda: {
        "name": "Apple M5", "vendor": "apple", "vram_gb": 17.76,
        "driver_version": "Metal 4", "cuda_version": "", "index": 0,
    })
    monkeypatch.setattr(hardware.shutil, "which", lambda _: pytest.fail(
        "looked for nvidia-smi on Apple Silicon"
    ))

    info = hardware.detect_hardware()
    assert info["ram_gb"] == 24.0
    assert [g["vendor"] for g in info["gpus"]] == ["apple"]


def test_detect_on_intel_mac_reports_no_apple_gpu(monkeypatch):
    _darwin(monkeypatch, "x86_64")
    monkeypatch.setattr(hardware, "_apple_gpu", lambda: pytest.fail(
        "Apple GPU entry produced on an Intel Mac"
    ))
    monkeypatch.setattr(hardware.shutil, "which", lambda _: None)

    assert hardware.detect_hardware()["gpus"] == []


# ─── Declared VRAM shapes registration too ──────────────────

def test_declared_vram_synthesises_entry_when_nothing_probed():
    gpus = hardware.apply_declared_vram([], 16.0, name="Radeon RX 7900")
    assert gpus == [{
        "name": "Radeon RX 7900", "vendor": "other", "vram_gb": 16.0,
        "driver_version": "", "cuda_version": "", "index": 0,
    }]


def test_declared_vram_overrides_single_probed_gpu():
    apple = [{"name": "Apple M5", "vendor": "apple", "vram_gb": 17.76, "index": 0}]
    assert hardware.apply_declared_vram(apple, 8.0)[0]["vram_gb"] == 8.0
    assert apple[0]["vram_gb"] == 17.76   # input not mutated


def test_declared_vram_scales_multiple_gpus_to_sum():
    gpus = [{"name": "a", "vram_gb": 24.0, "index": 0}, {"name": "b", "vram_gb": 8.0, "index": 1}]
    out = hardware.apply_declared_vram(gpus, 16.0)
    assert [g["vram_gb"] for g in out] == [12.0, 4.0]


def test_no_declaration_leaves_probed_list_alone():
    gpus = [{"name": "a", "vram_gb": 24.0, "index": 0}]
    assert hardware.apply_declared_vram(gpus, 0.0) is gpus


@pytest.mark.asyncio
async def test_registration_applies_declared_vram(monkeypatch, tmp_path):
    from daemon.registration import RegistrationManager
    from daemon.config import DaemonConfig

    monkeypatch.setattr("daemon.registration.detect_hardware", lambda: {
        "os": "Linux", "hostname": "amd-box", "cpu_cores": 8, "ram_gb": 32.0, "gpus": [],
    })
    seen = {}

    class _Client:
        async def register_worker(self, info):
            seen["gpus"] = [g.model_dump() for g in info.hardware.gpus]
            return {"worker_id": "w-1"}

    config = DaemonConfig(api_key="gk-x", vram_gb=16.0, gpu_name="Radeon RX 7900")
    manager = RegistrationManager(credentials_path=str(tmp_path / "creds"))
    await manager.register(_Client(), config)

    assert seen["gpus"][0]["vram_gb"] == 16.0
    assert seen["gpus"][0]["vendor"] == "other"


# ─── Heartbeat: silence and honesty ─────────────────────────

@pytest.mark.asyncio
async def test_zero_vram_warns_once(monkeypatch, caplog):
    monkeypatch.setattr(
        "daemon.heartbeat.get_gpu_utilization",
        lambda: {"utilization": 0.0, "memory_used_gb": 0.0, "memory_total_gb": 0.0},
    )
    manager = HeartbeatManager(client=None, worker_id="w1")
    with caplog.at_level(logging.WARNING, logger="daemon.heartbeat"):
        await manager._build_payload()
        await manager._build_payload()
    assert sum("Advertising 0 GB VRAM" in r.message for r in caplog.records) == 1


@pytest.mark.asyncio
async def test_available_unknown_when_used_unknown(monkeypatch):
    """Apple Silicon: no machine-wide 'used' → available is None, not total."""
    monkeypatch.setattr(
        "daemon.heartbeat.get_gpu_utilization",
        lambda: {"utilization": 0.0, "memory_used_gb": None, "memory_total_gb": 17.76},
    )
    payload = await HeartbeatManager(client=None, worker_id="w1")._build_payload()
    assert payload["vram_total_gb"] == 17.76
    assert payload["vram_available_gb"] is None
    assert payload["gpu_memory_used_gb"] == 0.0


@pytest.mark.asyncio
async def test_declared_vram_clamps_used(monkeypatch):
    """24 GB card, 20 GB in use, lent as 12 GB: not '0 free' for the lease."""
    monkeypatch.setattr(
        "daemon.heartbeat.get_gpu_utilization",
        lambda: {"utilization": 0.0, "memory_used_gb": 20.0, "memory_total_gb": 24.0},
    )
    manager = HeartbeatManager(client=None, worker_id="w1", declared_vram_gb=12.0)
    payload = await manager._build_payload()
    assert payload["vram_total_gb"] == 12.0
    assert payload["gpu_memory_used_gb"] == 12.0
    assert payload["vram_available_gb"] == 0.0


# ─── Registration payload carries the vendor ────────────────

def test_registration_payload_uses_gpu_vendor():
    from unittest.mock import patch
    import asyncio
    from daemon.client import BackendClient
    from daemon.models import GPUInfo, HardwareInfo, WorkerInfo

    hw = HardwareInfo(hostname="mac", gpus=[
        GPUInfo(name="Apple M5", vendor="apple", vram_gb=17.76, driver_version="Metal 4"),
    ])
    captured = {}

    class _Resp:
        def raise_for_status(self): pass
        def json(self): return {"worker_id": "w-1"}

    async def fake_post(url, json=None, **kw):
        captured["payload"] = json
        return _Resp()

    client = BackendClient(base_url="http://x", worker_id="w", api_key="gk-test")
    with patch.object(client, "_get_client", lambda: type("C", (), {"post": staticmethod(fake_post)})()):
        asyncio.run(client.register_worker(WorkerInfo(worker_id="w", hardware=hw)))

    assert captured["payload"]["gpus"][0]["vendor"] == "apple"
