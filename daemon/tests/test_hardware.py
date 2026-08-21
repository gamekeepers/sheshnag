"""Hardware detection — Apple Silicon support and the non-NVIDIA fallback.

Everything here mocks subprocess and the optional Metal import, so the suite
runs identically on a Linux CI box and on a Mac.
"""
import pytest

from daemon import hardware
from daemon.heartbeat import HeartbeatManager


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
    monkeypatch.setattr(hardware, "_apple_vram_gb", lambda: 17.76)
    monkeypatch.setattr(hardware.shutil, "which", lambda _: pytest.fail(
        "looked for nvidia-smi on macOS"
    ))

    stats = hardware.get_gpu_utilization()

    assert stats["memory_total_gb"] == 17.76
    # No machine-wide "in use" counter exists on unified memory.
    assert stats["memory_used_gb"] == 0.0


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
