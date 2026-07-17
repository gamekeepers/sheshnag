# GPU Worker Daemon

A lightweight polling daemon that connects to the central control plane, claims batch inference jobs, executes them via vLLM, and uploads results.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Worker Daemon                        │
│                                                         │
│  ┌──────────┐    ┌──────────┐    ┌─────────────────┐   │
│  │  Config   │───▶│  Worker  │◀───│ BackendClient   │   │
│  └──────────┘    │  (loop)  │    │ (HTTP to API)   │   │
│                  └────┬─────┘    └────────┬────────┘   │
│                       │                   │             │
│                       ▼                   │             │
│              ┌──────────────┐             │             │
│              │ BaseExecutor │             │             │
│              │   (ABC)      │             │             │
│              └──────┬───────┘             │             │
│                     │                     │             │
│              ┌──────▼───────┐             │             │
│              │ VLLMExecutor │             │             │
│              │ (OpenAI API) │             │             │
│              └──────────────┘             │             │
└─────────────────────────────────────────────────────────┘
         │                          │
         ▼                          ▼
  ┌──────────────┐         ┌──────────────┐
  │  vLLM Server │         │   Backend    │
  │  (localhost)  │         │  (FastAPI)   │
  └──────────────┘         └──────────────┘
```

## Design Principles

| Principle | Implementation |
|-----------|---------------|
| **Open/Closed** | Add new runtimes by subclassing `BaseExecutor` — zero changes to `Worker` |
| **Single Responsibility** | Each module owns one concern: config, HTTP, execution, orchestration |
| **Dependency Inversion** | `Worker` depends on `BaseExecutor` abstraction, not concrete `VLLMExecutor` |
| **Strategy Pattern** | Executor is injected at construction — swap runtimes without code changes |

## Quick Start

### Prerequisites

- Python 3.10+
- A running vLLM server (or mock backend for testing)

### Install

```bash
cd daemon
pip install -r requirements.txt
```

### Test WITHOUT vLLM (mock mode)

This runs the daemon against a mock backend and a mock vLLM to verify the full flow:

**Terminal 1 — Mock Backend:**
```bash
pip install fastapi uvicorn python-multipart
cd daemon
python -m tests.mock_backend
```

**Terminal 2 — Mock vLLM (optional — or use a real vLLM server):**
```bash
# If you have vLLM installed:
vllm serve mistralai/Mistral-7B-Instruct-v0.2 --port 8100

# If not, the daemon will log errors for each prompt but still
# demonstrate the full poll→download→execute→upload flow
```

**Terminal 3 — Daemon:**
```bash
cd daemon
python -m daemon.main --config config.yaml
```

### Test WITH real vLLM

```bash
# Terminal 1: Start vLLM
vllm serve mistralai/Mistral-7B-Instruct-v0.2 --port 8100

# Terminal 2: Start the real backend (when @nirav3690 has it ready)
# cd backend && uvicorn app.main:app --port 8000

# Terminal 3: Start daemon
cd daemon
python -m daemon.main \
  --backend-url http://localhost:8000 \
  --vllm-url http://localhost:8100
```

## Configuration

Configuration is loaded with this precedence (highest → lowest):
1. **CLI arguments** (`--backend-url`, `--vllm-url`, etc.)
2. **Environment variables** (`DAEMON_BACKEND_URL`, `DAEMON_VLLM_URL`, etc.)
3. **YAML config file** (`--config config.yaml`)
4. **Defaults**

### Config File (`config.yaml`)

```yaml
backend_url: "http://localhost:8000"
vllm_url: "http://localhost:8100"
poll_interval: 5
log_level: "INFO"
# worker_id: "my-gpu-01"  # auto-generated if omitted
```

### Environment Variables

```bash
export DAEMON_BACKEND_URL="http://api.example.com"
export DAEMON_VLLM_URL="http://localhost:8100"
export DAEMON_WORKER_ID="gpu-worker-01"
export DAEMON_POLL_INTERVAL=10
export DAEMON_LOG_LEVEL=DEBUG
```

### CLI Arguments

```bash
python -m daemon.main --help

# All options:
#   -c, --config        Path to YAML config file
#   --backend-url       Control plane API URL
#   --vllm-url          vLLM server URL
#   --worker-id         Unique worker ID
#   --poll-interval     Seconds between polls
#   --log-level         DEBUG|INFO|WARNING|ERROR
#   --work-dir          Job artifacts directory
```

## Project Structure

```
daemon/
├── daemon/                  # Python package
│   ├── __init__.py          # Version
│   ├── config.py            # Configuration management
│   ├── models.py            # Pydantic data models
│   ├── log.py               # Logging setup
│   ├── client.py            # Backend HTTP client
│   ├── worker.py            # Main poll-execute loop
│   ├── executors/           # Runtime backends (Strategy pattern)
│   │   ├── __init__.py
│   │   ├── base.py          # Abstract base executor
│   │   └── vllm.py          # vLLM implementation
│   └── main.py              # CLI entry point
├── tests/
│   ├── __init__.py
│   ├── sample_input.jsonl   # Test fixture
│   └── mock_backend.py      # Mock control plane server
├── config.yaml              # Default config
├── requirements.txt         # Dependencies
└── README.md                # This file
```

## API Contract (with Backend)

The daemon expects these endpoints from the backend:

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/workers/register` | POST | Register worker; returns the assigned `worker_id` |
| `/workers/{worker_id}/heartbeat` | POST | Liveness + dynamic capability stats (VRAM, loaded models) |
| `/workers/poll` | POST | Poll for available batches |
| `/v1/files/{id}/content` | GET | Download input JSONL (path from poll response) |
| `/workers/upload-results` | POST | Upload output JSONL |
| `/workers/report-failure` | POST | Report job failure |

All endpoints are authenticated with an **org worker API key** (`gk-...`),
created in the platform dashboard and configured on the daemon via
`--api-key` / `DAEMON_API_KEY` / `api_key` in `config.yaml`. The backend
derives the owning organization from the key; it never issues keys.

See [client.py](daemon/client.py) for full request/response details.

## Week 2+ Roadmap

The codebase is designed to support these additions without refactoring:

- **Heartbeats** → Add `heartbeat_loop()` in Worker, config already has `heartbeat_interval`
- **Checkpointing** → Save partial results in `_run_prompts()`, config has `checkpoint_interval`
- **New runtimes** → Subclass `BaseExecutor` (e.g., `OllamaExecutor`, `TGIExecutor`)
- **GPU metrics** → Add `gpu.py` module, report in heartbeat
- **Docker runtime** → Wrap executor in container management
- **Auth** → Add API key header in `BackendClient._get_client()`

## License

Internal project — Distributed Batch AI Compute Platform
