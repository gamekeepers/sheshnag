
| Information                  | Preferred तरीका                                           | Python package / command           |
| ---------------------------- | --------------------------------------------------------- | ---------------------------------- |
| Hostname                     | Python stdlib                                             | `socket`                           |
| OS                           | Python stdlib                                             | `platform`                         |
| CPU                          | Python stdlib                                             | `platform`, `psutil`               |
| RAM                          | `psutil`                                                  | `psutil.virtual_memory()`          |
| Disk space                   | `psutil`                                                  | `psutil.disk_usage()`              |
| GPU name                     | NVIDIA: `pynvml`; AMD: `rocm-smi`; Intel: `intel_gpu_top` | `pynvml`, subprocess               |
| GPU memory                   | `pynvml`                                                  | `nvmlDeviceGetMemoryInfo()`        |
| CUDA version                 | `pynvml` or `nvidia-smi`                                  | `nvmlSystemGetCudaDriverVersion()` |
| Driver version               | `pynvml`                                                  | `nvmlSystemGetDriverVersion()`     |
| Installed runtimes           | Detect executables                                        | `shutil.which()`                   |
| Installed models             | Runtime APIs                                              | Ollama/vLLM APIs                   |
| Network bandwidth (optional) | `psutil`                                                  | `psutil.net_if_stats()`            |

---

# Machine identity

```python
import socket
import platform
import uuid

info = {
    "hostname": socket.gethostname(),
    "os": platform.platform(),
    "machine": platform.machine(),
    "processor": platform.processor(),
    "worker_id": hex(uuid.getnode())
}
```

---

# RAM

```python
import psutil

ram = psutil.virtual_memory()

print({
    "total_gb": round(ram.total / 1024**3, 2),
    "available_gb": round(ram.available / 1024**3, 2)
})
```

---

# CPU

```python
import psutil

print({
    "cores_physical": psutil.cpu_count(False),
    "cores_logical": psutil.cpu_count(True)
})
```

---

# Disk

```python
import psutil

disk = psutil.disk_usage("/")

print({
    "total_gb": round(disk.total / 1024**3, 2),
    "free_gb": round(disk.free / 1024**3, 2)
})
```

---

# NVIDIA GPUs (recommended)

Install

```bash
pip install nvidia-ml-py
```

```python
from pynvml import *

nvmlInit()

gpus = []

for i in range(nvmlDeviceGetCount()):
    handle = nvmlDeviceGetHandleByIndex(i)

    mem = nvmlDeviceGetMemoryInfo(handle)

    gpus.append({
        "index": i,
        "name": nvmlDeviceGetName(handle),
        "vram_gb": round(mem.total / 1024**3, 2)
    })

print(gpus)
```

Output

```python
[
    {
        "index":0,
        "name":"NVIDIA GeForce RTX 4090",
        "vram_gb":24
    }
]
```

---

# CUDA version

```python
cuda = nvmlSystemGetCudaDriverVersion()

major = cuda // 1000
minor = (cuda % 1000) // 10

print(f"{major}.{minor}")
```

---

# Driver version

```python
driver = nvmlSystemGetDriverVersion()

print(driver.decode())
```

---

# Detect installed runtimes

```python
import shutil

runtimes = {}

for runtime in [
    "ollama",
    "vllm",
    "python",
    "docker",
]:
    runtimes[runtime] = shutil.which(runtime) is not None

print(runtimes)
```

---

# Ollama models

The easiest way is to call the local API.

```python
import requests

r = requests.get("http://localhost:11434/api/tags")

print(r.json())
```

You'll receive

```json
{
  "models":[
    {
      "name":"gemma4:27b",
      ...
    }
  ]
}
```

---

# vLLM models

```python
import requests

r = requests.get("http://localhost:8000/v1/models")

print(r.json())
```

---

# llama.cpp

If you start the server

```bash
llama-server
```

it also exposes

```
GET /v1/models
```

just like OpenAI.

---

# TGI

```text
GET /info
```

or

```text
GET /v1/models
```

depending on configuration.

---

# Docker

Useful later if you support containerized workers.

```python
import docker

client = docker.from_env()

print(client.version())
```

---

# Dynamic GPU utilization

Heartbeat every 30 seconds.

```python
util = nvmlDeviceGetUtilizationRates(handle)

print({
    "gpu_percent": util.gpu,
    "memory_percent": util.memory
})
```

---

# Recommended registration payload

I'd have the daemon send something like this:

```json
{
    "hostname": "gpu-box-01",
    "os": "Ubuntu 24.04",
    "cpu": {
        "cores": 16
    },
    "ram": {
        "total_gb": 64
    },
    "gpus": [
        {
            "index": 0,
            "vendor": "nvidia",
            "name": "RTX 4090",
            "vram_gb": 24,
            "driver": "575.64",
            "cuda": "12.8"
        }
    ],
    "runtimes": [
        {
            "type": "ollama",
            "endpoint": "http://localhost:11434",
            "models": [
                "gemma4:27b",
                "qwen3:30b"
            ]
        },
        {
            "type": "vllm",
            "endpoint": "http://localhost:8000",
            "models": [
                "google/gemma-4-27b-it"
            ]
        }
    ]
}
```

## One design improvement

Instead of having the daemon inspect model directories or infer runtime details, treat each runtime as a **plugin** with a common interface:

```python
class RuntimePlugin:
    def detect(self) -> bool: ...
    def list_models(self) -> list[Model]: ...
    def health(self) -> RuntimeStatus: ...
```

Then implement plugins like:

* `OllamaPlugin`
* `VLLMPlugin`
* `LlamaCppPlugin`
* `TGIPlugin`

The daemon simply discovers installed plugins and aggregates their output. This keeps the worker extensible—adding support for a new runtime becomes a matter of adding one new plugin rather than modifying the core daemon.
