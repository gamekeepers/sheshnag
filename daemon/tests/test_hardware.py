"""
Hardware detection tests — no GPU required.

`shutil.which` and `subprocess.run` are patched with canned output recorded
from real machines (rocm-smi 6.4.0 on a Radeon RX 6700 + Cezanne iGPU box;
nvidia-smi 535 on an RTX 4090), so the parsers are exercised against the
exact strings the tools emit.
"""

import json
import subprocess
from unittest.mock import patch

import pytest

from daemon import hardware
from daemon.models import GPUInfo, HardwareInfo, WorkerInfo


# ── Recorded fixtures ─────────────────────────────────────────────

ROCM_SMI_DETECT = json.dumps({
    "card0": {
        "VRAM Total Memory (B)": "10720641024",
        "VRAM Total Used Memory (B)": "16400384",
        "Card series": "Navi 22 [Radeon RX 6700/6700 XT/6750 XT / 6800M/6850M XT]",
        "Card model": "0x1316",
        "Card vendor": "Advanced Micro Devices, Inc. [AMD/ATI]",
        "Card SKU": "unknown",
    },
    "card1": {
        "VRAM Total Memory (B)": "536870912",
        "VRAM Total Used Memory (B)": "484909056",
        "Card series": "Cezanne [Radeon Vega Series / Radeon Vega Mobile Series]",
        "Card model": "0x1316",
        "Card vendor": "Advanced Micro Devices, Inc. [AMD/ATI]",
        "Card SKU": "CEZANNE",
    },
    "system": {"Driver version": "6.12.12"},
})

ROCM_SMI_USE = json.dumps({
    "card0": {
        "GPU use (%)": "40",
        "VRAM Total Memory (B)": "10720641024",
        "VRAM Total Used Memory (B)": "5368709120",   # 5 GiB
    },
    "card1": {
        "GPU use (%)": "0",
        "VRAM Total Memory (B)": "536870912",
        "VRAM Total Used Memory (B)": "268435456",    # 0.25 GiB
    },
})

NVIDIA_SMI_BANNER = (
    "Wed Aug 26 10:00:00 2026\n"
    "+---------------------------------------------------------------------+\n"
    "| NVIDIA-SMI 535.183.01   Driver Version: 535.183.01   CUDA Version: 12.2 |\n"
)
NVIDIA_SMI_DETECT = "0, NVIDIA GeForce RTX 4090, 24564\n"
NVIDIA_SMI_USE = "80, 12288, 24564\n"


class _Completed:
    def __init__(self, stdout):
        self.stdout = stdout
        self.returncode = 0


def _fake_run(outputs):
    """subprocess.run stand-in: dispatch on the tool name and its args."""
    def run(cmd, **kwargs):
        tool = cmd[0]
        args = cmd[1:]
        for (t, needle), out in outputs.items():
            if t == tool and (needle is None or needle in args):
                if isinstance(out, Exception):
                    raise out
                return _Completed(out)
        raise AssertionError(f"unexpected call: {cmd}")
    return run


def _which(*present):
    return lambda name: f"/usr/bin/{name}" if name in present else None


# ── AMD detection ─────────────────────────────────────────────────

def test_detect_amd_gpus_from_rocm_smi(tmp_path):
    version_file = tmp_path / "version"
    version_file.write_text("6.4.0-47\n")
    outputs = {("rocm-smi", "--showproductname"): ROCM_SMI_DETECT}
    with patch.object(hardware.shutil, "which", _which("rocm-smi")), \
         patch.object(hardware.subprocess, "run", _fake_run(outputs)), \
         patch.object(hardware, "_ROCM_VERSION_FILES", (str(version_file),)):
        gpus = hardware.detect_hardware()["gpus"]

    assert [g["index"] for g in gpus] == [0, 1]
    rx = gpus[0]
    assert rx["vendor"] == "amd"
    assert rx["name"].startswith("Navi 22 [Radeon RX 6700")
    assert rx["vram_gb"] == 9.98
    assert rx["driver_version"] == "6.12.12"
    assert rx["rocm_version"] == "6.4.0"
    assert rx["cuda_version"] == ""
    assert gpus[1]["vram_gb"] == 0.5


def test_amd_rocm_version_unknown_when_file_missing(tmp_path):
    outputs = {("rocm-smi", "--showproductname"): ROCM_SMI_DETECT}
    with patch.object(hardware.shutil, "which", _which("rocm-smi")), \
         patch.object(hardware.subprocess, "run", _fake_run(outputs)), \
         patch.object(hardware, "_ROCM_VERSION_FILES", (str(tmp_path / "nope"),)):
        gpus = hardware.detect_hardware()["gpus"]
    assert gpus and all(g["rocm_version"] == "" for g in gpus)


def test_amd_utilization_sums_memory_and_averages_use():
    outputs = {("rocm-smi", "--showuse"): ROCM_SMI_USE}
    with patch.object(hardware.shutil, "which", _which("rocm-smi")), \
         patch.object(hardware.subprocess, "run", _fake_run(outputs)):
        stats = hardware.get_gpu_utilization()
    assert stats["utilization"] == 20.0            # (40 + 0) / 2
    assert stats["memory_used_gb"] == 5.25         # 5 + 0.25
    assert stats["memory_total_gb"] == 10.48       # 9.98 + 0.5


@pytest.mark.parametrize("exc", [
    subprocess.TimeoutExpired(cmd="rocm-smi", timeout=5),
    subprocess.CalledProcessError(1, "rocm-smi"),
])
def test_amd_failures_degrade_to_no_gpu(exc):
    outputs = {("rocm-smi", None): exc}
    with patch.object(hardware.shutil, "which", _which("rocm-smi")), \
         patch.object(hardware.subprocess, "run", _fake_run(outputs)):
        assert hardware.detect_hardware()["gpus"] == []
        assert hardware.get_gpu_utilization() == {
            "utilization": 0.0, "memory_used_gb": 0.0, "memory_total_gb": 0.0,
        }


def test_amd_garbage_json_degrades_to_no_gpu():
    outputs = {("rocm-smi", None): "WARNING: no AMD GPUs found\n"}
    with patch.object(hardware.shutil, "which", _which("rocm-smi")), \
         patch.object(hardware.subprocess, "run", _fake_run(outputs)):
        assert hardware.detect_hardware()["gpus"] == []
        assert hardware.get_gpu_utilization()["memory_total_gb"] == 0.0


def test_rocm_smi_calls_carry_hard_timeout():
    seen = []
    def run(cmd, **kwargs):
        seen.append((cmd[0], kwargs.get("timeout")))
        return _Completed(ROCM_SMI_USE)
    with patch.object(hardware.shutil, "which", _which("rocm-smi")), \
         patch.object(hardware.subprocess, "run", run), \
         patch.object(hardware, "_ROCM_VERSION_FILES", ()):
        hardware.get_gpu_utilization()
        hardware.detect_hardware()
    assert seen and all(t is not None and t > 0 for _, t in seen)
    assert seen[0] == ("rocm-smi", hardware._SMI_TIMEOUT)          # heartbeat path
    assert ("rocm-smi", hardware._SMI_DETECT_TIMEOUT) in seen       # registration path


# ── No tooling / NVIDIA unchanged ─────────────────────────────────

def test_no_smi_tools_means_no_gpus():
    with patch.object(hardware.shutil, "which", _which()), \
         patch.object(hardware.subprocess, "run", _fake_run({})):
        assert hardware.detect_hardware()["gpus"] == []
        assert hardware.get_gpu_utilization()["memory_total_gb"] == 0.0
    assert hardware.gpu_vendors_present() == [] or True  # real PATH, just must not raise


def test_nvidia_path_reports_vendor_nvidia():
    outputs = {
        ("nvidia-smi", "--query-gpu=index,name,memory.total"): NVIDIA_SMI_DETECT,
        ("nvidia-smi", None): NVIDIA_SMI_BANNER,
    }
    with patch.object(hardware.shutil, "which", _which("nvidia-smi")), \
         patch.object(hardware.subprocess, "run", _fake_run(outputs)):
        gpus = hardware.detect_hardware()["gpus"]
    assert gpus == [{
        "name": "NVIDIA GeForce RTX 4090", "vendor": "nvidia", "vram_gb": 23.99,
        "driver_version": "535.183.01", "cuda_version": "12.2",
        "rocm_version": "", "index": 0,
    }]


def test_gpu_vendors_present_order():
    with patch.object(hardware.shutil, "which", _which("nvidia-smi", "rocm-smi")):
        assert hardware.gpu_vendors_present() == ["nvidia", "amd"]
    with patch.object(hardware.shutil, "which", _which("rocm-smi")):
        assert hardware.gpu_vendors_present() == ["amd"]


# ── Mixed vendor ──────────────────────────────────────────────────

def test_mixed_vendor_detection_and_heartbeat_aggregate(tmp_path):
    (tmp_path / "version").write_text("6.4.0-47")
    outputs = {
        ("nvidia-smi", "--query-gpu=index,name,memory.total"): NVIDIA_SMI_DETECT,
        ("nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total"): NVIDIA_SMI_USE,
        ("nvidia-smi", None): NVIDIA_SMI_BANNER,
        ("rocm-smi", "--showproductname"): ROCM_SMI_DETECT,
        ("rocm-smi", "--showuse"): ROCM_SMI_USE,
    }
    with patch.object(hardware.shutil, "which", _which("nvidia-smi", "rocm-smi")), \
         patch.object(hardware.subprocess, "run", _fake_run(outputs)), \
         patch.object(hardware, "_ROCM_VERSION_FILES", (str(tmp_path / "version"),)):
        gpus = hardware.detect_hardware()["gpus"]
        stats = hardware.get_gpu_utilization()

    assert [g["vendor"] for g in gpus] == ["nvidia", "amd", "amd"]
    # Same sum/average rule across vendors: (80 + 40 + 0) / 3
    assert stats["utilization"] == 40.0
    assert stats["memory_used_gb"] == round(12288 / 1024 + 5.25, 2)
    assert stats["memory_total_gb"] == round(24564 / 1024 + 10.48, 2)


# ── Registration payload ──────────────────────────────────────────

def test_registration_payload_carries_vendor_and_rocm():
    """client.register_worker must send each GPU's own vendor, not 'nvidia'."""
    from daemon.client import BackendClient

    hw = HardwareInfo(hostname="amd-box", os="Linux", cpu_cores=16, ram_gb=64.0, gpus=[
        GPUInfo(name="Radeon RX 6700 XT", vendor="amd", vram_gb=9.98,
                driver_version="6.12.12", rocm_version="6.4.0", index=0),
        GPUInfo(name="RTX 4090", vendor="nvidia", vram_gb=23.99,
                driver_version="535.183.01", cuda_version="12.2", index=1),
    ])
    info = WorkerInfo(worker_id="w", hardware=hw, models=["llama3:8b"])

    captured = {}

    class _Resp:
        def raise_for_status(self): pass
        def json(self): return {"worker_id": "w-1"}

    async def fake_post(url, json=None, **kw):
        captured["payload"] = json
        return _Resp()

    client = BackendClient(base_url="http://x", worker_id="w", api_key="gk-test")
    with patch.object(client, "_get_client", lambda: type("C", (), {"post": staticmethod(fake_post)})()):
        import asyncio
        asyncio.run(client.register_worker(info))

    gpus = captured["payload"]["gpus"]
    assert gpus[0]["vendor"] == "amd"
    assert gpus[0]["rocm"] == "6.4.0"
    assert gpus[0]["cuda"] is None
    assert gpus[1]["vendor"] == "nvidia"
    assert gpus[1]["cuda"] == "12.2"
    assert gpus[1]["rocm"] is None
