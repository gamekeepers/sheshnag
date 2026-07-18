> **📜 HISTORICAL (Week-1 plan).** Kept for context. The "assumed"
> contracts below have since changed: poll returns `input_file_id` +
> `input_path` (input downloads via `GET /v1/files/{id}/content`, not
> `/jobs/{id}/input`); auth, heartbeats, progress reporting, and an
> Ollama executor — all "out of scope" here — are implemented. See
> [daemon/README.md](daemon/README.md) for the current contract.

# Week 1 — Worker Daemon Implementation Plan

## Scope

Build a **polling worker daemon** in Python that:
1. Polls `POST /workers/poll` for jobs
2. Downloads input JSONL from backend
3. Sends each prompt to a local vLLM server (OpenAI-compatible API)
4. Collects responses into `output.jsonl`
5. Uploads results via `POST /workers/upload-results`
6. Loops back

**Not in scope for Week 1**: auth, checkpointing, resumability, Docker, GPU metrics, multi-model, heartbeats.

---

## Design Principles Applied

| Principle | How |
|-----------|-----|
| **Open/Closed** | `BaseExecutor` ABC — add new runtimes (Ollama, TGI) by subclassing, never modifying `Worker` |
| **Single Responsibility** | Each class owns one concern: config, HTTP client, executor, orchestration |
| **Dependency Inversion** | `Worker` depends on `BaseExecutor` abstraction, not `VLLMExecutor` directly |
| **Liskov Substitution** | Any `BaseExecutor` subclass is a drop-in replacement |
| **Strategy Pattern** | Executor is swappable at construction time via DI |

---

## Project Structure

```
daemon/
├── daemon/                    # Python package
│   ├── __init__.py            # Package version
│   ├── config.py              # DaemonConfig (Pydantic) — YAML + env + CLI
│   ├── models.py              # Data models: Job, PromptRequest, CompletionResult
│   ├── log.py                 # Structured logging setup
│   ├── client.py              # BackendClient — all HTTP calls to control plane
│   ├── worker.py              # Worker — main poll→execute→upload loop
│   ├── executors/
│   │   ├── __init__.py
│   │   ├── base.py            # BaseExecutor ABC
│   │   └── vllm.py            # VLLMExecutor (OpenAI-compatible HTTP)
│   └── main.py                # CLI entry point
├── config.yaml                # Default configuration
├── requirements.txt
└── README.md
```

---

## API Contract Assumptions (with @nirav3690's backend)

### `POST /workers/poll`
```
Request:  {"worker_id": "worker-abc123"}
Response (job found):
{
  "job": {
    "job_id": "uuid",
    "model": "mistralai/Mistral-7B-Instruct-v0.2",
    "status": "assigned",
    "max_tokens": 512,
    "temperature": 0.7
  }
}
Response (no job): 204 No Content
```

### `GET /jobs/{job_id}/input`
```
Response: raw JSONL file content
```

### `POST /workers/upload-results`
```
Request: multipart/form-data
  - job_id: string
  - file: output.jsonl
Response: {"status": "ok"}
```

> [!IMPORTANT]
> These are **assumed** contracts. The daemon is coded defensively so it can adapt to minor changes from the backend team.

---

## vLLM Integration

**Approach**: vLLM runs as a separate process (`vllm serve <model>`). The daemon talks to it via the OpenAI-compatible REST API at `http://localhost:8000/v1/chat/completions`.

The daemon does NOT start/stop vLLM. That's @Akshay/@Ankush's responsibility. The daemon just expects vLLM to be reachable.

---

## Verification Plan

### Unit Testing
- Mock backend client + mock executor → test Worker loop logic
- Parse sample JSONL → verify PromptRequest parsing

### Integration Testing
- Run daemon against a mock FastAPI server (simple script provided)
- Verify full flow: poll → download → execute → upload

### Manual Testing
- Start vLLM locally
- Start backend locally
- Run daemon
- Submit a job
- Verify output file is correct
