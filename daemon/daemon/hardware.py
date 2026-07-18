import logging
import platform
import subprocess
import os
import re
import shutil
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

# nvidia-smi answers in <100ms when healthy; a hard timeout keeps a wedged
# driver from hanging the caller (get_gpu_utilization runs every heartbeat).
_SMI_TIMEOUT = 5
_SMI_DETECT_TIMEOUT = 10  # one-off at registration, can afford more


def _run_smi(args: List[str], timeout: int = _SMI_TIMEOUT) -> str:
    """Run nvidia-smi with a hard timeout; raises on failure/timeout."""
    return subprocess.run(
        ["nvidia-smi", *args],
        capture_output=True, text=True, timeout=timeout, check=True,
    ).stdout


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
        try:
            cpu_model = subprocess.check_output(["sysctl", "-n", "machdep.cpu.brand_string"], timeout=_SMI_DETECT_TIMEOUT).decode().strip()
            info["cpu_model"] = cpu_model
            mem_bytes = int(subprocess.check_output(["sysctl", "-n", "hw.memsize"], timeout=_SMI_DETECT_TIMEOUT).decode().strip())
            info["ram_gb"] = round(mem_bytes / (1024**3), 2)
        except Exception:
            pass

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
    """
    stats = {
        "utilization": 0.0,
        "memory_used_gb": 0.0,
        "memory_total_gb": 0.0
    }
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
