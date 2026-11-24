"""GPU memory as nvidia-smi reports it, host-wide.

Read the memory numbers, never the process list: inside a rootless
container nvidia-smi shows "No running processes found" even when another
user's vLLM holds 43 GB of the card.
"""
import socket
import subprocess
from typing import Optional


def memory_gb() -> tuple[Optional[float], Optional[float]]:
    """(total, free) in GB for GPU 0, or (None, None) without nvidia-smi."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total,memory.free", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout.splitlines()[0]
        total, free = (float(x) / 1024 for x in out.split(","))
        return round(total, 2), round(free, 2)
    except (OSError, subprocess.SubprocessError, IndexError, ValueError):
        return None, None


def describe() -> list[dict]:
    """GPU list for /workers/register."""
    try:
        rows = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,name,memory.total,driver_version",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        return []
    gpus = []
    for row in rows:
        index, name, total, driver = (x.strip() for x in row.split(","))
        gpus.append({"index": int(index), "name": name,
                     "vram_gb": round(float(total) / 1024, 2), "driver": driver})
    return gpus


def hostname() -> str:
    return socket.gethostname()
