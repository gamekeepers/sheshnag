import functools
import json
import logging
import platform
import subprocess
import os
import re
import shutil
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

# nvidia-smi answers in <100ms when healthy; a hard timeout keeps a wedged
# driver from hanging the caller (get_gpu_utilization runs every heartbeat).
_SMI_TIMEOUT = 5
_SMI_DETECT_TIMEOUT = 10  # one-off at registration, can afford more

# Fraction of total RAM Apple Silicon may wire for the GPU, used only when
# Metal cannot be queried. macOS computes the real cap with an undocumented
# formula that varies by chip — on an M5/24GB it lands at 0.74, not the 2/3
# or 3/4 quoted in the wild. Deliberately conservative: under-advertising
# wastes capacity, over-advertising accepts models that will not fit.
# Providers who want the exact figure install pyobjc or set DAEMON_VRAM_GB.
_APPLE_VRAM_FALLBACK_RATIO = 0.66


def _run_smi(args: List[str], timeout: int = _SMI_TIMEOUT) -> str:
    """Run nvidia-smi with a hard timeout; raises on failure/timeout."""
    return subprocess.run(
        ["nvidia-smi", *args],
        capture_output=True, text=True, timeout=timeout, check=True,
    ).stdout


# ─── Apple Silicon (Metal) ──────────────────────────────────

def _sysctl(name: str) -> Optional[str]:
    """Read a single sysctl value as text, or None if unreadable."""
    try:
        return subprocess.run(
            ["sysctl", "-n", name],
            capture_output=True, text=True, timeout=_SMI_TIMEOUT, check=True,
        ).stdout.strip()
    except (subprocess.SubprocessError, OSError) as e:
        logger.debug(f"sysctl {name} failed: {e}")
        return None


def _metal_vram_gb() -> Optional[float]:
    """GPU memory ceiling straight from Metal, or None if unavailable.

    `recommendedMaxWorkingSetSize` is Apple's own answer to "how much may
    this GPU hold", already accounting for any `iogpu.wired_limit_mb`
    override. It is the same number Ollama reports at startup. Requires
    pyobjc-framework-Metal, which is macOS-only and therefore optional.
    """
    try:
        import Metal  # noqa: PLC0415 — optional, macOS-only
        device = Metal.MTLCreateSystemDefaultDevice()
        if device is None:
            return None
        return round(device.recommendedMaxWorkingSetSize() / (1024 ** 3), 2)
    except Exception as e:
        logger.debug(f"Metal VRAM query unavailable: {e}")
        return None


@functools.lru_cache(maxsize=1)
def _apple_vram_gb() -> float:
    """Usable GPU memory on Apple Silicon, in GiB. Computed once per process.

    Cached because the heartbeat asks every 30 s for a value that only
    changes on reboot or a wired-limit change — and because a transient
    Metal hiccup must not silently switch the source mid-run (17.76 from
    Metal vs 15.84 from the ratio would flap models in and out of the
    scheduler's eligibility and disagree with what registration advertised).

    Apple Silicon has no dedicated VRAM — CPU and GPU share one pool — so
    the meaningful number is the cap macOS puts on how much of it the GPU
    may wire. Sources, best first:
      1. Metal's `recommendedMaxWorkingSetSize` (exact).
      2. `iogpu.wired_limit_mb`, when an operator has set one explicitly.
      3. A conservative fraction of `hw.memsize`.
    """
    from_metal = _metal_vram_gb()
    if from_metal:
        return from_metal

    wired_mb = _sysctl("iogpu.wired_limit_mb")
    if wired_mb and wired_mb.isdigit() and int(wired_mb) > 0:
        return round(int(wired_mb) / 1024, 2)

    memsize = _sysctl("hw.memsize")
    if memsize and memsize.isdigit():
        return round(int(memsize) / (1024 ** 3) * _APPLE_VRAM_FALLBACK_RATIO, 2)

    return 0.0


def _apple_gpu() -> Optional[Dict[str, Any]]:
    """The built-in Apple GPU as a registration entry, or None.

    Only called from detect_hardware(): system_profiler takes about a
    second, which is fine once at startup and far too slow for the
    heartbeat path (see get_gpu_utilization, which uses sysctl instead).
    """
    name, cores, metal = "Apple GPU", None, None
    try:
        out = subprocess.run(
            ["system_profiler", "-json", "SPDisplaysDataType"],
            capture_output=True, text=True,
            timeout=_SMI_DETECT_TIMEOUT, check=True,
        ).stdout
        for entry in json.loads(out).get("SPDisplaysDataType", []):
            if entry.get("sppci_bus") != "spdisplays_builtin":
                continue  # an external/discrete display adapter, not the SoC GPU
            name = entry.get("sppci_model") or entry.get("_name") or name
            cores = entry.get("sppci_cores")
            # "spdisplays_metal4" → "Metal 4"
            family = entry.get("spdisplays_mtlgpufamilysupport", "")
            m = re.search(r"metal(\d+)", family)
            metal = f"Metal {m.group(1)}" if m else None
            break
    except (subprocess.SubprocessError, OSError, ValueError, KeyError,
            TypeError, AttributeError) as e:
        # system_profiler renders some fields as lists or nests them; any
        # shape surprise degrades to the name-less entry below, never raises.
        logger.debug(f"system_profiler GPU detection failed: {e}")

    vram_gb = _apple_vram_gb()
    if not vram_gb:
        return None  # nothing useful to advertise

    return {
        "name": f"{name} ({cores}-core GPU)" if cores else name,
        "vendor": "apple",
        "vram_gb": vram_gb,
        "driver_version": metal or "",
        "cuda_version": "",   # Metal, not CUDA
        "index": 0,
    }


def detect_hardware() -> Dict[str, Any]:
    """
    Detects hardware specifications of the system.
    Returns a dictionary matching the HardwareInfo model.
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
        cpu_model = _sysctl("machdep.cpu.brand_string")
        if cpu_model:
            info["cpu_model"] = cpu_model
        memsize = _sysctl("hw.memsize")
        if memsize and memsize.isdigit():
            info["ram_gb"] = round(int(memsize) / (1024 ** 3), 2)

        # Apple Silicon only (arm64): no nvidia-smi will ever exist here, and
        # the SoC GPU is real and Metal-capable. Reporting no GPU makes the
        # scheduler's VRAM guard reject this worker for every batch.
        # Intel Macs deliberately fall through to the nvidia-smi probe: their
        # iGPU has no unified-memory ceiling, and the ratio fallback would
        # advertise ~2/3 of RAM as VRAM for a 1.5 GB iGPU.
        if platform.machine() == "arm64":
            apple = _apple_gpu()
            if apple:
                info["gpus"].append(apple)
            return info

    # Detect GPUs via nvidia-smi
    if shutil.which("nvidia-smi") is None:
        return info
    try:
        # Get Driver and CUDA versions
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

        # Get GPU details
        # Format: index, name, memory.total
        csv_out = _run_smi([
            "--query-gpu=index,name,memory.total", "--format=csv,noheader,nounits"
        ], timeout=_SMI_DETECT_TIMEOUT).strip()

        for line in csv_out.split('\n'):
            if not line.strip():
                continue
            parts = [p.strip() for p in line.split(',')]
            if len(parts) >= 3:
                gpu_idx = int(parts[0])
                gpu_name = parts[1]
                vram_mb = float(parts[2])

                info["gpus"].append({
                    "name": gpu_name,
                    "vendor": "nvidia",
                    "vram_gb": round(vram_mb / 1024, 2),
                    "driver_version": driver_version,
                    "cuda_version": cuda_version,
                    "index": gpu_idx
                })
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, ValueError) as e:
        logger.debug(f"GPU detection via nvidia-smi failed: {e}")

    return info

def get_gpu_utilization() -> Dict[str, Any]:
    """
    Returns real-time GPU utilization and memory usage.

    Multi-GPU: memory is SUMMED across GPUs and utilization averaged —
    the scheduler matches models against total machine VRAM, which is
    valid for runtimes that split layers across GPUs (Ollama, vLLM with
    tensor parallelism) and mirrors what registration advertises.

    `memory_used_gb` is None when the platform exposes no machine-wide
    "in use" counter (Apple Silicon); callers must not treat that as 0.
    """
    stats = {
        "utilization": 0.0,
        "memory_used_gb": 0.0,
        "memory_total_gb": 0.0
    }

    if platform.system() == "Darwin" and platform.machine() == "arm64":
        # Unified memory exposes no machine-wide "GPU memory in use" counter —
        # there is no equivalent of nvidia-smi's memory.used, and Metal's
        # currentAllocatedSize only sees this process. Report the ceiling and
        # mark used as unknown (None) rather than a confident 0 that the
        # dashboard would render as "fully free". Per-model residency would
        # have to come from the runtime (Ollama's /api/ps reports size_vram),
        # which belongs at a different layer than this.
        stats["memory_total_gb"] = _apple_vram_gb()
        stats["memory_used_gb"] = None
        return stats

    if shutil.which("nvidia-smi") is None:
        return stats
    try:
        csv_out = _run_smi([
            "--query-gpu=utilization.gpu,memory.used,memory.total",
            "--format=csv,noheader,nounits",
        ]).strip()

        utils: List[float] = []
        used_mb = total_mb = 0.0
        for line in csv_out.split('\n'):
            if not line.strip():
                continue
            parts = [p.strip() for p in line.split(',')]
            if len(parts) >= 3:
                utils.append(float(parts[0]))
                used_mb += float(parts[1])
                total_mb += float(parts[2])
        if utils:
            stats["utilization"] = round(sum(utils) / len(utils), 1)
            stats["memory_used_gb"] = round(used_mb / 1024, 2)
            stats["memory_total_gb"] = round(total_mb / 1024, 2)
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, ValueError) as e:
        logger.debug(f"GPU stats via nvidia-smi failed: {e}")

    return stats


def apply_declared_vram(gpus: List[Dict[str, Any]], declared_gb: float,
                        name: str = "unknown") -> List[Dict[str, Any]]:
    """
    Reconcile probed GPUs with an operator-declared capacity (DAEMON_VRAM_GB).

    Used by registration so the advertised hardware agrees with what the
    heartbeat reports; the declaration is the total the scheduler may use:
      - no declaration → probed list unchanged;
      - nothing probed  → one synthetic entry (vendor "other") so an
        unprobeable host (AMD/ROCm, Intel, CPU-only) is not a GPU-less worker;
      - one GPU         → its vram_gb becomes the declaration;
      - several GPUs    → scaled proportionally so they sum to the declaration.
    """
    if not declared_gb or declared_gb <= 0:
        return gpus
    if not gpus:
        return [{
            "name": name,
            "vendor": "other",
            "vram_gb": round(declared_gb, 2),
            "driver_version": "",
            "cuda_version": "",
            "index": 0,
        }]
    probed_total = sum(g.get("vram_gb", 0.0) for g in gpus)
    out = []
    for g in gpus:
        g = dict(g)
        if len(gpus) == 1 or probed_total <= 0:
            g["vram_gb"] = round(declared_gb / len(gpus), 2)
        else:
            g["vram_gb"] = round(declared_gb * g.get("vram_gb", 0.0) / probed_total, 2)
        out.append(g)
    return out
