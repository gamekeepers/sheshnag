import json
import logging
import platform
import subprocess
import os
import re
import shutil
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

# nvidia-smi / rocm-smi answer in well under a second when healthy; a hard
# timeout keeps a wedged driver from hanging the caller
# (get_gpu_utilization runs every heartbeat).
_SMI_TIMEOUT = 5
_SMI_DETECT_TIMEOUT = 10  # one-off at registration, can afford more

# Where ROCm records its own version. The kernel driver version rocm-smi
# reports is the amdgpu module, which is a different number.
_ROCM_VERSION_FILES = (
    "/opt/rocm/.info/version",
    "/opt/rocm/.info/version-dev",
)


def _run_cmd(cmd: str, args: List[str], timeout: int = _SMI_TIMEOUT) -> str:
    """Run a vendor SMI tool with a hard timeout; raises on failure/timeout."""
    return subprocess.run(
        [cmd, *args],
        capture_output=True, text=True, timeout=timeout, check=True,
    ).stdout


def _run_smi(args: List[str], timeout: int = _SMI_TIMEOUT) -> str:
    """Run nvidia-smi with a hard timeout; raises on failure/timeout."""
    return _run_cmd("nvidia-smi", args, timeout=timeout)


def _run_rocm_smi(args: List[str], timeout: int = _SMI_TIMEOUT) -> str:
    """Run rocm-smi with a hard timeout; raises on failure/timeout."""
    return _run_cmd("rocm-smi", args, timeout=timeout)


# ── NVIDIA ─────────────────────────────────────────────────────────


def _detect_nvidia_gpus() -> List[Dict[str, Any]]:
    """GPUs visible to nvidia-smi; [] when it is absent or fails."""
    if shutil.which("nvidia-smi") is None:
        return []
    gpus: List[Dict[str, Any]] = []
    try:
        # Example output snippet: Driver Version: 535.183.01   CUDA Version: 12.2
        smi_out = _run_smi([], timeout=_SMI_DETECT_TIMEOUT)
        driver_version = ""
        cuda_version = ""

        driver_match = re.search(r"Driver Version:\s+([0-9.]+)", smi_out)
        if driver_match:
            driver_version = driver_match.group(1)

        cuda_match = re.search(r"CUDA Version:\s+([0-9.]+)", smi_out)
        if cuda_match:
            cuda_version = cuda_match.group(1)

        # Format: index, name, memory.total
        csv_out = _run_smi([
            "--query-gpu=index,name,memory.total", "--format=csv,noheader,nounits"
        ], timeout=_SMI_DETECT_TIMEOUT).strip()

        for line in csv_out.split('\n'):
            if not line.strip():
                continue
            parts = [p.strip() for p in line.split(',')]
            if len(parts) >= 3:
                gpus.append({
                    "name": parts[1],
                    "vendor": "nvidia",
                    "vram_gb": round(float(parts[2]) / 1024, 2),
                    "driver_version": driver_version,
                    "cuda_version": cuda_version,
                    "rocm_version": "",
                    "index": int(parts[0]),
                })
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, ValueError) as e:
        logger.debug(f"GPU detection via nvidia-smi failed: {e}")
    return gpus


def _nvidia_utilization() -> List[Dict[str, float]]:
    """Per-GPU (utilization %, used MB, total MB) from nvidia-smi."""
    if shutil.which("nvidia-smi") is None:
        return []
    samples: List[Dict[str, float]] = []
    try:
        csv_out = _run_smi([
            "--query-gpu=utilization.gpu,memory.used,memory.total",
            "--format=csv,noheader,nounits",
        ]).strip()
        for line in csv_out.split('\n'):
            if not line.strip():
                continue
            parts = [p.strip() for p in line.split(',')]
            if len(parts) >= 3:
                samples.append({
                    "utilization": float(parts[0]),
                    "used_mb": float(parts[1]),
                    "total_mb": float(parts[2]),
                })
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, ValueError) as e:
        logger.debug(f"GPU stats via nvidia-smi failed: {e}")
    return samples


# ── AMD (ROCm) ─────────────────────────────────────────────────────
#
# rocm-smi --json emits one object per card keyed "card0", "card1", ...
# plus an optional "system" entry, every value a string:
#
#   {"card0": {"VRAM Total Memory (B)": "10720641024",
#              "VRAM Total Used Memory (B)": "16400384",
#              "Card series": "Navi 22 [Radeon RX 6700/6700 XT ...]",
#              "GPU use (%)": "0", ...},
#    "system": {"Driver version": "6.12.12"}}
#
# Keys are human labels and have drifted between ROCm releases, so lookups
# below match on a prefix rather than the exact string.

_CARD_KEY = re.compile(r"^card(\d+)$")


def _rocm_field(card: Dict[str, Any], *prefixes: str) -> Optional[str]:
    """First value whose key starts with any of `prefixes` (case-insensitive)."""
    lowered = {k.lower(): v for k, v in card.items()}
    for prefix in prefixes:
        p = prefix.lower()
        for k, v in lowered.items():
            if k.startswith(p):
                return str(v)
    return None


def _parse_rocm_json(raw: str) -> Dict[int, Dict[str, Any]]:
    """{card index: card dict} from rocm-smi --json output."""
    data = json.loads(raw)
    cards: Dict[int, Dict[str, Any]] = {}
    for key, value in data.items():
        m = _CARD_KEY.match(key)
        if m and isinstance(value, dict):
            cards[int(m.group(1))] = value
    return cards


def _rocm_version() -> str:
    """Installed ROCm release, e.g. '6.4.0'; '' when unknown."""
    for path in _ROCM_VERSION_FILES:
        try:
            with open(path) as f:
                text = f.read().strip()
        except OSError:
            continue
        m = re.match(r"(\d+(?:\.\d+)+)", text)  # '6.4.0-47' -> '6.4.0'
        if m:
            return m.group(1)
    return ""


def _detect_amd_gpus() -> List[Dict[str, Any]]:
    """GPUs visible to rocm-smi; [] when it is absent or fails."""
    if shutil.which("rocm-smi") is None:
        return []
    gpus: List[Dict[str, Any]] = []
    try:
        raw = _run_rocm_smi(
            ["--showproductname", "--showmeminfo", "vram", "--showdriverversion", "--json"],
            timeout=_SMI_DETECT_TIMEOUT,
        )
        data = json.loads(raw)
        system = data.get("system") if isinstance(data.get("system"), dict) else {}
        driver_version = _rocm_field(system, "Driver version") or ""
        rocm_version = _rocm_version()

        for idx, card in sorted(_parse_rocm_json(raw).items()):
            name = (
                _rocm_field(card, "Card series")
                or _rocm_field(card, "Card SKU")
                or _rocm_field(card, "Card model")
                or "AMD GPU"
            )
            total_b = _rocm_field(card, "VRAM Total Memory")
            gpus.append({
                "name": name,
                "vendor": "amd",
                "vram_gb": round(float(total_b) / (1024 ** 3), 2) if total_b else 0.0,
                "driver_version": driver_version,
                "cuda_version": "",
                "rocm_version": rocm_version,
                "index": idx,
            })
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError,
            ValueError, json.JSONDecodeError) as e:
        logger.debug(f"GPU detection via rocm-smi failed: {e}")
    return gpus


def _amd_utilization() -> List[Dict[str, float]]:
    """Per-GPU (utilization %, used MB, total MB) from rocm-smi."""
    if shutil.which("rocm-smi") is None:
        return []
    samples: List[Dict[str, float]] = []
    try:
        raw = _run_rocm_smi(["--showuse", "--showmeminfo", "vram", "--json"])
        for _idx, card in sorted(_parse_rocm_json(raw).items()):
            use = _rocm_field(card, "GPU use")
            total_b = _rocm_field(card, "VRAM Total Memory")
            used_b = _rocm_field(card, "VRAM Total Used Memory")
            if total_b is None:
                continue
            samples.append({
                "utilization": float(use) if use not in (None, "", "N/A") else 0.0,
                "used_mb": float(used_b) / (1024 ** 2) if used_b else 0.0,
                "total_mb": float(total_b) / (1024 ** 2),
            })
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError,
            ValueError, json.JSONDecodeError) as e:
        logger.debug(f"GPU stats via rocm-smi failed: {e}")
    return samples


# ── Public API ─────────────────────────────────────────────────────


def gpu_vendors_present() -> List[str]:
    """
    Vendors whose management tool is on PATH, in detection order.

    Cheap (no subprocess) — safe to call from startup code that must not
    block. Presence of the tool is a strong hint, not proof, that a GPU of
    that vendor exists; use detect_hardware() for the real inventory.
    """
    vendors: List[str] = []
    if shutil.which("nvidia-smi") is not None:
        vendors.append("nvidia")
    if shutil.which("rocm-smi") is not None:
        vendors.append("amd")
    return vendors



def detect_hardware() -> Dict[str, Any]:
    """
    Detects hardware specifications of the system.
    Returns a dictionary matching the HardwareInfo model.

    GPUs are probed per vendor (nvidia-smi, then rocm-smi) and concatenated;
    a machine with both simply reports both. Each GPU carries its own
    `vendor` so the registration payload never has to guess.
    """
    info = {
        "os": platform.system(),
        "os_version": platform.version(),
        "kernel": platform.release(),
        "arch": platform.machine(),
        "hostname": platform.node(),
        "cpu_model": "Unknown CPU",
        "cpu_cores": os.cpu_count() or 0,
        "ram_gb": 0.0,
        "gpus": []
    }

    # Detect CPU and RAM on Linux
    if info["os"] == "Linux":
        try:
            with open("/proc/cpuinfo", "r") as f:
                for line in f:
                    if line.startswith("model name"):
                        info["cpu_model"] = line.split(":", 1)[1].strip()
                        break
        except Exception:
            pass

        try:
            with open("/proc/meminfo", "r") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        # MemTotal:       32768000 kB
                        kb = int(line.split()[1])
                        info["ram_gb"] = round(kb / (1024 * 1024), 2)
                        break
        except Exception:
            pass
    elif info["os"] == "Darwin": # macOS fallback
        try:
            cpu_model = subprocess.check_output(["sysctl", "-n", "machdep.cpu.brand_string"], timeout=_SMI_DETECT_TIMEOUT).decode().strip()
            info["cpu_model"] = cpu_model
            mem_bytes = int(subprocess.check_output(["sysctl", "-n", "hw.memsize"], timeout=_SMI_DETECT_TIMEOUT).decode().strip())
            info["ram_gb"] = round(mem_bytes / (1024**3), 2)
        except Exception:
            pass

    info["gpus"] = _detect_nvidia_gpus() + _detect_amd_gpus()
    return info


def get_gpu_utilization() -> Dict[str, Any]:
    """
    Returns real-time GPU utilization and memory usage.

    Multi-GPU: memory is SUMMED across GPUs and utilization averaged —
    the scheduler matches models against total machine VRAM, which is
    valid for runtimes that split layers across GPUs (Ollama, vLLM with
    tensor parallelism) and mirrors what registration advertises.
    Mixed-vendor machines aggregate with the same rule.
    """
    stats = {
        "utilization": 0.0,
        "memory_used_gb": 0.0,
        "memory_total_gb": 0.0
    }
    samples = _nvidia_utilization() + _amd_utilization()
    if samples:
        stats["utilization"] = round(
            sum(s["utilization"] for s in samples) / len(samples), 1)
        stats["memory_used_gb"] = round(sum(s["used_mb"] for s in samples) / 1024, 2)
        stats["memory_total_gb"] = round(sum(s["total_mb"] for s in samples) / 1024, 2)
    return stats
