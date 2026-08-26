# GPU Worker Daemon

A lightweight polling daemon that connects to the central control plane, claims batch inference jobs, executes them via a local runtime (**Ollama** by default, or **vLLM**), and uploads results — reporting heartbeats, capability stats, and live progress along the way.

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                        Worker Daemon                         │
│                                                              │
│  ┌────────┐   ┌──────────┐   ┌───────────────┐               │
│  │ Config  │──▶│  Worker  │◀──│ BackendClient │               │
│  └────────┘   │  (loop)  │   │ (HTTP to API) │               │
│               └──┬───┬───┘   └───────┬───────┘               │
│                  │   │               │                       │
│   ┌──────────────┘   └────────┐      │                       │
│   ▼                          ▼      │                       │
│ ┌───────────────┐   ┌──────────────┐ │                       │
│ │ HeartbeatMgr  │   │ BaseExecutor │ │                       │
│ │ (30s, stats)  │   │    (ABC)     │ │                       │
│ └───────────────┘   └──────┬───────┘ │                       │
│                    ┌───────┴────────┐│                       │
│                    ▼                ▼│                       │
│           ┌────────────────┐ ┌──────────────┐                │
│           │ OllamaExecutor │ │ VLLMExecutor │                │
│           │   (default)    │ │ (OpenAI API) │                │
│           └────────────────┘ └──────────────┘                │
└──────────────────────────────────────────────────────────────┘
          │                                  │
          ▼                                  ▼
   ┌──────────────┐                  ┌──────────────┐
   │ Ollama/vLLM  │                  │   Backend    │
   │ (localhost)  │                  │  (FastAPI)   │
   └──────────────┘                  └──────────────┘
```

## Design Principles

| Principle | Implementation |
|-----------|---------------|
| **Open/Closed** | Add new runtimes by subclassing `BaseExecutor` — zero changes to `Worker` |
| **Single Responsibility** | Each module owns one concern: config, HTTP, execution, heartbeats, registration, orchestration |
| **Dependency Inversion** | `Worker` depends on `BaseExecutor`; the concrete executor is chosen by `executor_factory` from `runtime` config |
| **Strategy Pattern** | Executor injected at construction — swap runtimes without code changes |

## Quick Start

### Prerequisites

- Python 3.10+
- A running **Ollama** (default) or **vLLM** server
- An **org worker API key** (`gk-...`), created in the platform dashboard

### Install

```bash
cd daemon
pip install -r requirements.txt
```

Or use the guided installer from the repo root: `scripts/install.sh` —
**rootless by design**: no sudo at any step, everything under
`~/.gpu-daemon/` (code, venv, config, user-local Ollama), services via
`systemctl --user` with linger so they survive logout. Prompts for the
API key (or reads `BACKEND_URL`/`API_KEY`/`WORKER_ID` env vars for
non-interactive installs).

### Test WITHOUT a real backend (mock mode)

**Terminal 1 — Mock Backend:**
```bash
cd daemon
pip install -r tests/requirements.txt
python -m tests.mock_backend
```

**Terminal 2 — Runtime (either):**
```bash
ollama serve                     # default runtime, port 11434
# or
vllm serve mistralai/Mistral-7B-Instruct-v0.2 --port 8100
```

**Terminal 3 — Daemon:**
```bash
cd daemon
python -m daemon.main --config config.yaml --api-key gk-anything-for-mock
```

### Run against the real backend

```bash
cd daemon
python -m daemon.main \
  --backend-url http://localhost:8000 \
  --api-key gk-your-org-worker-key
```

The daemon exits with an error if no API key is configured. On successful
registration the backend assigns a `worker_id`, persisted (with the key)
in `~/.gpu-daemon/credentials`; if a later re-registration fails, the
daemon falls back to the saved id.

## Configuration

Precedence (highest → lowest):
1. **CLI arguments**
2. **Environment variables** (`DAEMON_*`)
3. **YAML config file** (`--config config.yaml`)
4. **Defaults**

### Config File (`config.yaml`)

```yaml
backend_url: "http://localhost:8000"
api_key: "gk-your-org-worker-key"
runtime: "ollama"                  # or "vllm"
ollama_url: "http://localhost:11434"
vllm_url: "http://localhost:8100"
poll_interval: 5
heartbeat_interval: 30
inference_timeout: 300.0
log_level: "INFO"
# worker_id is assigned by the backend at registration
```

### CLI arguments / environment variables

| CLI flag | Env var | Meaning |
|---|---|---|
| `-c, --config` | — | Path to YAML config file |
| `--backend-url` | `DAEMON_BACKEND_URL` | Control plane API URL |
| `--api-key` | `DAEMON_API_KEY` | Org worker API key (required) |
| `--runtime` | `DAEMON_RUNTIME` | `ollama` (default) or `vllm` |
| `--ollama-url` | `DAEMON_OLLAMA_URL` | Ollama server URL |
| `--vllm-url` | `DAEMON_VLLM_URL` | vLLM server URL |
| `--worker-id` | `DAEMON_WORKER_ID` | Override (normally backend-assigned) |
| `--poll-interval` | `DAEMON_POLL_INTERVAL` | Seconds between polls |
| `--heartbeat-interval` | `DAEMON_HEARTBEAT_INTERVAL` | Seconds between heartbeats |
| `--inference-timeout` | `DAEMON_INFERENCE_TIMEOUT` | Per-prompt timeout (s), any runtime |
| `--models` | — | Models advertised at registration |
| `--gpu-name` / `--vram-gb` | `DAEMON_GPU_NAME` / `DAEMON_VRAM_GB` | Registration metadata overrides |
| `--log-level` | `DAEMON_LOG_LEVEL` | DEBUG / INFO / WARNING / ERROR |
| `--work-dir` | `DAEMON_WORK_DIR` | Job artifacts directory |

## Project Structure

```
daemon/
├── daemon/
│   ├── __init__.py          # Version
│   ├── config.py            # DaemonConfig — YAML + env + CLI precedence
│   ├── models.py            # Job, PromptRequest, CompletionResult, WorkerInfo
│   ├── log.py               # Logging setup
│   ├── client.py            # BackendClient — all control-plane HTTP
│   ├── worker.py            # Poll → download → execute → upload loop
│   ├── heartbeat.py         # HeartbeatManager (activity + capability stats)
│   ├── hardware.py          # GPU/CPU/RAM inspection (nvidia-smi, rocm-smi)
│   ├── registration.py      # Registration + credential persistence
│   ├── model_manager.py     # Ollama model pulls (on-the-fly downloads)
│   ├── executor_factory.py  # runtime config → executor instance
│   ├── executors/
│   │   ├── base.py          # BaseExecutor ABC
│   │   ├── ollama.py        # OllamaExecutor (default)
│   │   └── vllm.py          # VLLMExecutor (OpenAI-compatible)
│   └── main.py              # CLI entry point
├── tests/
│   ├── sample_input.jsonl
│   ├── mock_backend.py      # Mock control plane (mirrors real contract)
│   └── mock_vllm.py         # Mock inference server
├── config.yaml
├── requirements.txt
└── README.md
```

## API Contract (with Backend)

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/workers/register` | POST | Register; backend assigns and returns `worker_id` |
| `/workers/{worker_id}/heartbeat` | POST | Liveness + `activity` (idle/busy/downloading_model) + VRAM/loaded-model stats |
| `/workers/poll` | POST | Poll for available batches |
| `/v1/files/{id}/content` | GET | Download input JSONL (path from poll response) |
| `/workers/progress` | POST | Live prompt counts every 10 prompts |
| `/workers/model-progress` | POST | Model download progress (Ollama pulls) |
| `/workers/upload-results` | POST | Upload output JSONL + `worker_id` + real completed/failed counts |
| `/workers/report-failure` | POST | Report failure — backend requeues (max 3 attempts) |

All endpoints are authenticated with an **org worker API key** (`gk-...`),
created in the platform dashboard and configured via `--api-key` /
`DAEMON_API_KEY` / `api_key` in `config.yaml`. The backend derives the
owning organization from the key; it never issues keys. If the daemon
stops heartbeating, the backend's sweeper marks the worker offline and
requeues its in-flight batch.

See [client.py](daemon/client.py) for full request/response details.

## License

Internal project — Distributed Batch AI Compute Platform
