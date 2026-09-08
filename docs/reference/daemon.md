# Worker daemon internals

How the daemon is built and how it talks to the control plane. For **installing**
one, read [Lend your GPU](../provider.md) instead — this page is for people
changing the daemon's code.

*Verified against code: 2026-08-26.*

A lightweight polling daemon: it asks the control plane for work, downloads the
input file, runs the prompts through a local runtime (**Ollama** by default, or
**vLLM**), uploads results, and reports heartbeats and progress throughout.

---

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
│   ├── hardware.py          # GPU/CPU/RAM inspection (nvidia-smi etc.)
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
| `/workers/progress` | POST | Live prompt counts, time-throttled (default 5s) plus a guaranteed final report |
| `/workers/model-progress` | POST | Model download progress (Ollama pulls) |
| `/workers/upload-results` | POST | Upload output JSONL + `worker_id` + real completed/failed counts |
| `/workers/report-failure` | POST | Report failure — backend requeues (max 3 attempts) |

All endpoints are authenticated with an **org worker API key** (`gk-...`),
created in the platform dashboard and configured via `--api-key` /
`DAEMON_API_KEY` / `api_key` in `config.yaml`. The backend derives the
owning organization from the key; it never issues keys. If the daemon
stops heartbeating, the backend's sweeper marks the worker offline and
requeues its in-flight batch.

See [`client.py`](https://github.com/gamekeepers/sheshnag/blob/develop/daemon/daemon/client.py)
for full request/response details.

## Executing a batch

Prompts within a batch run through a bounded pool of concurrent workers, sized
by `max_concurrent_prompts` (default 8). Decode is memory-bandwidth bound, so a
single sequence leaves most of the card idle; running several at once reads the
model's weights once per step and shares them, which is where the throughput
comes from.

The pool is fixed for the life of the job — the daemon does not yet measure the
runtime's real capacity and size itself to it.

Embedding rows go through the same pool. Where the runtime can serve several in
one request, a chunk of them is one unit of work; where it cannot, each row is
scheduled individually and gets the pool's concurrency rather than running
serially after the chat prompts. How many rows go in a chunk is the executor's
`embedding_chunk_size` — 64 on Ollama, whose `/api/embed` accepts a list of
inputs, and 1 (no coalescing) by default.

The guarantee the pool keeps is that **every input row produces exactly one
output row, in input order.** Prompts finish out of order and the output file is
still written in order, and no single row can fail the job:

| Situation | Result |
|---|---|
| Prompt fails in the runtime | That row carries the runtime's error |
| Unexpected exception mid-execution | Only that unit's rows fail, with `INTERNAL_ERROR` |
| `stream: true` on any endpoint | That row fails with `UNSUPPORTED_PARAMETER` |
| Repeated `custom_id` | The second and later rows fail with `DUPLICATE_CUSTOM_ID` |
| Shutdown signal | Prompts in flight finish; the rest are simply absent |

Rejections are all decided in one pass before execution starts, so an input
cannot get different treatment depending on which runtime it lands on. A
repeated `custom_id` is already rejected by the backend's validator at upload,
so that row only appears on a bypass — it fails the row rather than the job,
which is what the pre-pool sequential loop did.

## Configuration

Precedence, highest first: **CLI arguments**, then environment variables
(`DAEMON_*`), then the YAML config file, then defaults. The mapping lives in
`_ENV_MAP` in `daemon/daemon/config.py`.

Every variable and flag is tabulated in
[Configuration](configuration.md#daemon-daemon).

## Where the daemon fits in a batch's life

The daemon only ever moves a batch between two states. Everything else is the
control plane's:

- It claims a batch that is already `validated`, which the backend flips to
  `in_progress`.
- It ends that batch as `completed` (results uploaded) or `failed` (failure
  reported).

A failure it reports does not necessarily end the batch — the backend requeues
it back to `validated` for another worker, up to three attempts. The same
happens without the daemon's involvement if it simply stops heartbeating. The
full state machine, and the constants behind it, are in the
[API reference](api.md#batch-status-lifecycle).
