import platform
import subprocess
import os
import re
from typing import Dict, Any, List

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
            cpu_model = subprocess.check_output(["sysctl", "-n", "machdep.cpu.brand_string"]).decode().strip()
            info["cpu_model"] = cpu_model
            mem_bytes = int(subprocess.check_output(["sysctl", "-n", "hw.memsize"]).decode().strip())
            info["ram_gb"] = round(mem_bytes / (1024**3), 2)
        except Exception:
            pass

    # Detect GPUs via nvidia-smi
    try:
        # Check if nvidia-smi exists
        if subprocess.call(["which", "nvidia-smi"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0:
            # Get Driver and CUDA versions
            # Example output snippet: Driver Version: 535.183.01   CUDA Version: 12.2
            smi_out = subprocess.check_output(["nvidia-smi"], stderr=subprocess.STDOUT).decode()
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
            csv_out = subprocess.check_output([
                "nvidia-smi", "--query-gpu=index,name,memory.total", "--format=csv,noheader,nounits"
            ]).decode().strip()

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
    except Exception:
        pass

    return info

def get_gpu_utilization() -> Dict[str, Any]:
    """
    Returns real-time GPU utilization and memory usage.
    """
    stats = {
        "utilization": 0.0,
        "memory_used_gb": 0.0
    }
    try:
        if subprocess.call(["which", "nvidia-smi"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0:
            csv_out = subprocess.check_output([
                "nvidia-smi", "--query-gpu=utilization.gpu,memory.used", "--format=csv,noheader,nounits"
            ]).decode().strip()
            
            # If multiple GPUs, average utilization, sum memory (or just take the first for now)
            lines = [l for l in csv_out.split('\n') if l.strip()]
            if lines:
                parts = [p.strip() for p in lines[0].split(',')]
                if len(parts) >= 2:
                    # e.g., "0", "1024"
                    stats["utilization"] = float(parts[0])
                    stats["memory_used_gb"] = round(float(parts[1]) / 1024, 2)
    except Exception:
        pass
        
    return stats
