# Daemon Review Fixes — Worker Registration, Auth & Code Quality

Address all code review feedback from the PR review, focusing on: **Worker Registration**, **Authentication**, and **Code Quality** issues. Heartbeat, GPU stats, and checkpointing are deferred to Week 2.

---

## Proposed Changes

### 1. Models — `WorkerInfo` + Job Fixes

#### [MODIFY] [models.py](file:///Users/foundationofmac/Documents/Vatsal%20Codes/vLLM%20Inference/daemon/daemon/models.py)

**New `WorkerInfo` model** for registration (spec §8):
```python
class WorkerInfo(BaseModel):
    worker_id: str
    gpu_name: str = "unknown"
    vram_gb: float = 0.0
    models: List[str] = Field(default_factory=list)
    runtime: str = "vllm"
    status: str = "online"
```

**Reconcile `JobStatus` enum** — remove `ASSIGNED` since the spec says `queued → running → completed | failed`. The backend sends `"running"` when a job is claimed. The daemon should trust whatever the backend returns.

```diff
 class JobStatus(str, Enum):
     QUEUED = "queued"
-    ASSIGNED = "assigned"
     RUNNING = "running"
     COMPLETED = "completed"
     FAILED = "failed"
```

**Add `input_file` to `Job` model** (spec §9):
```diff
 class Job(BaseModel):
     job_id: str
     model: str = ""
+    input_file: Optional[str] = None
-    status: JobStatus = JobStatus.ASSIGNED
+    status: JobStatus = JobStatus.QUEUED
     max_tokens: int = 512
     temperature: float = 0.7
```

**Fix `CompletionResult.usage` return type** — allow `int | float`:
```diff
     @property
-    def usage(self) -> Dict[str, int]:
+    def usage(self) -> Dict[str, int | float]:
```

---

### 2. Config — Registration Fields, Auth, Validators, DRY Refactor

#### [MODIFY] [config.py](file:///Users/foundationofmac/Documents/Vatsal%20Codes/vLLM%20Inference/daemon/daemon/config.py)

**Add registration and auth fields**:
```python
# Registration metadata (spec §8)
gpu_name: str = "unknown"
vram_gb: float = 0.0
models: List[str] = Field(default_factory=list)
runtime: str = "vllm"

# Authentication (spec §17)
api_key: Optional[str] = None

# vLLM executor timeout (configurable)
vllm_timeout: float = 300.0
```

**Add Pydantic validators** for numeric fields:
```python
poll_interval: int = Field(default=5, gt=0, description="Must be > 0")
vram_gb: float = Field(default=0.0, ge=0)
vllm_timeout: float = Field(default=300.0, gt=0)
```

**Include hostname in worker_id** for debuggability:
```python
def _generate_worker_id() -> str:
    hostname = socket.gethostname()
    return f"worker-{hostname}-{uuid.uuid4().hex[:8]}"
```

**Eliminate dead code / duplication** — extract a shared `_ENV_MAP` and have `from_env()` and `load()` both use it. Have `load()` accept an optional `cli_overrides` dict to consolidate the precedence logic that's currently split across `config.py` and `main.py`.

Unified env mapping:
```python
_ENV_MAP: dict[str, str] = {
    "worker_id":      "DAEMON_WORKER_ID",
    "backend_url":    "DAEMON_BACKEND_URL",
    "vllm_url":       "DAEMON_VLLM_URL",
    "poll_interval":  "DAEMON_POLL_INTERVAL",
    "log_level":      "DAEMON_LOG_LEVEL",
    "work_dir":       "DAEMON_WORK_DIR",
    "api_key":        "DAEMON_API_KEY",
    "gpu_name":       "DAEMON_GPU_NAME",
    "vram_gb":        "DAEMON_VRAM_GB",
    "vllm_timeout":   "DAEMON_VLLM_TIMEOUT",
}

_INT_FIELDS = {"poll_interval"}
_FLOAT_FIELDS = {"vram_gb", "vllm_timeout"}
_LIST_FIELDS = {"models"}
```

Consolidated `load()`:
```python
@classmethod
def load(
    cls,
    config_path: Optional[str] = None,
    cli_overrides: Optional[Dict[str, Any]] = None,
) -> DaemonConfig:
    """
    Smart loader: YAML → env → CLI → defaults.
    Single source of truth for config precedence.
    """
    base = {}
    # Layer 1: YAML
    if config_path and Path(config_path).exists():
        with open(config_path) as fh:
            base.update(yaml.safe_load(fh) or {})
    # Layer 2: Env
    base.update(cls._read_env())
    # Layer 3: CLI (highest)
    if cli_overrides:
        base.update({k: v for k, v in cli_overrides.items() if v is not None})
    return cls(**base)
```

---

### 3. Client — Auth, Registration, Failure Reporting, Connection Pooling, Retry

#### [MODIFY] [client.py](file:///Users/foundationofmac/Documents/Vatsal%20Codes/vLLM%20Inference/daemon/daemon/client.py)

**Add auth support** — accept `api_key` and inject `Authorization: Bearer <key>` header:
```python
def __init__(self, base_url: str, worker_id: str, api_key: Optional[str] = None):
    ...
    self._api_key = api_key
```

**Configure connection pooling** for long-running daemon:
```python
self._client = httpx.AsyncClient(
    base_url=self._base_url,
    timeout=httpx.Timeout(_DEFAULT_TIMEOUT),
    limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
    headers=headers,
    transport=httpx.AsyncHTTPTransport(retries=3),
)
```

**Add `register_worker()` method** — `POST /workers/register`:
```python
async def register_worker(self, worker_info: WorkerInfo) -> None:
    """Register this worker with the control plane (spec §8)."""
    client = self._get_client()
    response = await client.post(
        "/workers/register",
        json=worker_info.model_dump(),
    )
    response.raise_for_status()
    logger.info(f"Worker registered: {worker_info.worker_id}")
```

**Add `report_failure()` method** — notify backend when a job fails:
```python
async def report_failure(self, job_id: str, error: str) -> None:
    """Report job failure to the backend so it can requeue."""
    client = self._get_client()
    try:
        response = await client.post(
            f"/jobs/{job_id}/fail",
            json={"worker_id": self._worker_id, "error": error[:2000]},
        )
        response.raise_for_status()
        logger.info(f"Reported failure for job {job_id}")
    except Exception as exc:
        logger.warning(f"Failed to report failure for job {job_id}: {exc}")
```

**Reduce poll timeout** from 30s → 10s (lightweight call, fail fast):
```python
response = await client.post(
    "/workers/poll",
    json={"worker_id": self._worker_id},
    timeout=httpx.Timeout(10.0),
)
```

---

### 4. Worker — Jitter, Narrower Catches, Failure Reporting, Empty Input Fix

#### [MODIFY] [worker.py](file:///Users/foundationofmac/Documents/Vatsal%20Codes/vLLM%20Inference/daemon/daemon/worker.py)

**Add poll jitter** (±20%) to prevent thundering herd:
```python
import random

jitter = self._config.poll_interval * random.uniform(0.8, 1.2)
await asyncio.sleep(jitter)
```

**Narrow exception catching** in main loop — catch only recoverable errors, let fatal ones (`MemoryError`, `SystemExit`) propagate:
```python
except (httpx.HTTPError, OSError, ValueError, json.JSONDecodeError) as exc:
    logger.error(f"Recoverable error in main loop: {exc}", exc_info=True)
```

**Report failure to backend** when a job fails:
```python
except Exception as exc:
    logger.error(f"Job {job.job_id} failed: {exc}", exc_info=True)
    await self._client.report_failure(job.job_id, str(exc))
```

**Fix empty input handling** — upload an empty result file or report failure instead of silently returning:
```python
if total == 0:
    logger.warning(f"[{job.job_id}] Input file is empty — reporting failure")
    await self._client.report_failure(job.job_id, "Input file is empty")
    return
```

---

### 5. Executors — Connection Pooling, Configurable Timeout, Model Validation

#### [MODIFY] [vllm.py](file:///Users/foundationofmac/Documents/Vatsal%20Codes/vLLM%20Inference/daemon/daemon/executors/vllm.py)

**Add connection pooling** to vLLM client:
```python
self._client = httpx.AsyncClient(
    base_url=self._base_url,
    timeout=httpx.Timeout(self._timeout),
    limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
)
```

**Accept model info** for validation (spec §10):
```python
def __init__(self, base_url: str, timeout: float = 300.0, supported_models: list[str] | None = None):
    ...
    self._supported_models = set(supported_models or [])
```

**Validate model in health_check** — check `/v1/models` response body for expected models, not just status code:
```python
async def health_check(self) -> bool:
    """Verify vLLM is reachable and expected models are loaded."""
    ...
    # If models are configured, verify they appear in /v1/models response
    if self._supported_models:
        resp = await client.get("/v1/models", timeout=10.0)
        loaded = {m["id"] for m in resp.json().get("data", [])}
        if not self._supported_models.issubset(loaded):
            missing = self._supported_models - loaded
            logger.warning(f"Models not loaded in vLLM: {missing}")
            return False
```

#### [MODIFY] [base.py](file:///Users/foundationofmac/Documents/Vatsal%20Codes/vLLM%20Inference/daemon/daemon/executors/base.py)

**Fix docstring inconsistency** — comment says `shutdown()` but method is `close()`:
```diff
-     - `shutdown()` for cleanup
+     - `close()` for cleanup
```

---

### 6. Main — Registration Call, Config Consolidation, Remove Duplicate Signal Handling

#### [MODIFY] [main.py](file:///Users/foundationofmac/Documents/Vatsal%20Codes/vLLM%20Inference/daemon/daemon/main.py)

**Add worker registration** before starting the poll loop:
```python
# Register with the control plane
worker_info = WorkerInfo(
    worker_id=config.worker_id,
    gpu_name=config.gpu_name,
    vram_gb=config.vram_gb,
    models=config.models,
    runtime=config.runtime,
    status="online",
)
await client.register_worker(worker_info)
```

**Add CLI args** for new config fields (`--api-key`, `--gpu-name`, `--vram-gb`, `--models`, `--runtime`, `--vllm-timeout`).

**Consolidate config loading** — use `DaemonConfig.load(config_path, cli_overrides)` instead of building overrides separately in main.py.

**Remove duplicate `KeyboardInterrupt` catch** — the signal handler in `worker.py` already handles `SIGINT`. Keep only the `asyncio.run()` wrapper with a cleaner fallback:
```python
try:
    asyncio.run(_run(config))
except KeyboardInterrupt:
    pass  # Signal handler already logged the shutdown
```

**Log new fields** in the startup banner (models, GPU, auth status).

---

### 7. Mock Backend — Support Registration and Auth

#### [MODIFY] [mock_backend.py](file:///Users/foundationofmac/Documents/Vatsal%20Codes/vLLM%20Inference/daemon/tests/mock_backend.py)

**Add `/workers/register` endpoint**:
```python
@app.post("/workers/register")
async def register_worker(body: dict):
    worker_id = body.get("worker_id", "unknown")
    _workers[worker_id] = body
    print(f"✅ Worker registered: {worker_id} (GPU: {body.get('gpu_name')}, Models: {body.get('models')})")
    return JSONResponse(content={"status": "registered", "worker_id": worker_id})
```

**Add `/jobs/{job_id}/fail` endpoint**:
```python
@app.post("/jobs/{job_id}/fail")
async def report_failure(job_id: str, body: dict):
    if job_id in _jobs:
        _jobs[job_id]["status"] = "failed"
        _jobs[job_id]["error"] = body.get("error", "unknown")
    return JSONResponse(content={"status": "ok"})
```

**Add optional auth header validation** (warn if missing, don't block for testing).

---

### 8. Config YAML — Add New Fields

#### [MODIFY] [config.yaml](file:///Users/foundationofmac/Documents/Vatsal%20Codes/vLLM%20Inference/daemon/config.yaml)

Add commented examples for all new config fields:
```yaml
# Worker authentication (spec §17)
# api_key: "your-api-key-here"

# GPU metadata (spec §8) — used during worker registration
# gpu_name: "RTX 4090"
# vram_gb: 24.0

# Models available on this worker
# models:
#   - "mistralai/Mistral-7B-Instruct-v0.2"

# Runtime type
# runtime: "vllm"

# vLLM inference timeout per prompt (seconds)
# vllm_timeout: 300.0
```

---

## Summary of Changes by File

| File | Changes |
|------|---------|
| [models.py](file:///Users/foundationofmac/Documents/Vatsal%20Codes/vLLM%20Inference/daemon/daemon/models.py) | Add `WorkerInfo`, remove `ASSIGNED` status, add `input_file` to Job, fix `usage` type |
| [config.py](file:///Users/foundationofmac/Documents/Vatsal%20Codes/vLLM%20Inference/daemon/daemon/config.py) | Add registration/auth fields, Pydantic validators, DRY env mapping, consolidated `load()`, hostname in worker_id |
| [client.py](file:///Users/foundationofmac/Documents/Vatsal%20Codes/vLLM%20Inference/daemon/daemon/client.py) | Auth headers, connection pooling, retry transport, `register_worker()`, `report_failure()`, shorter poll timeout |
| [worker.py](file:///Users/foundationofmac/Documents/Vatsal%20Codes/vLLM%20Inference/daemon/daemon/worker.py) | Poll jitter, narrower exception catching, failure reporting, empty input fix |
| [vllm.py](file:///Users/foundationofmac/Documents/Vatsal%20Codes/vLLM%20Inference/daemon/daemon/executors/vllm.py) | Connection pooling, configurable timeout, model validation |
| [base.py](file:///Users/foundationofmac/Documents/Vatsal%20Codes/vLLM%20Inference/daemon/daemon/executors/base.py) | Fix docstring |
| [main.py](file:///Users/foundationofmac/Documents/Vatsal%20Codes/vLLM%20Inference/daemon/daemon/main.py) | Registration call, new CLI args, consolidated config loading, remove duplicate KeyboardInterrupt |
| [mock_backend.py](file:///Users/foundationofmac/Documents/Vatsal%20Codes/vLLM%20Inference/daemon/tests/mock_backend.py) | Add `/workers/register`, `/jobs/{job_id}/fail` endpoints, auth header check |
| [config.yaml](file:///Users/foundationofmac/Documents/Vatsal%20Codes/vLLM%20Inference/daemon/config.yaml) | Add commented examples for new fields |

---

## Open Questions

> [!IMPORTANT]
> **1. Auth flow details** — The spec says "signed API keys". Should the daemon just send a static `Bearer` token, or does it need a more complex flow (e.g., register → receive token → use token)?  I'm implementing a simple `Bearer <api_key>` pattern for now, which covers the review ask. Let me know if your sir expects something more.

> [!IMPORTANT]
> **2. `ASSIGNED` status removal** — The mock backend currently sends `"status": "assigned"` in poll responses. Removing `ASSIGNED` from the enum means the daemon will only accept `queued/running/completed/failed`. Should the mock also change to send `"running"` instead? (I'll do this by default.)

> [!NOTE]
> **3. `models` config field** — This is a list of model names the worker has loaded. Should this be auto-detected from vLLM's `/v1/models` endpoint at startup, or manually configured? I'll support both: manual config + optional auto-discovery from vLLM.

---

## Verification Plan

### Automated Tests
1. **Syntax check**: `python -m py_compile daemon/daemon/*.py daemon/daemon/executors/*.py`
2. **Import check**: `python -c "from daemon.main import main"` — verify all modules load without errors
3. **Mock integration test**: Start `mock_backend.py` + `mock_vllm.py` + daemon, verify:
   - Registration call happens before polling
   - Auth header is sent on all requests
   - Jobs complete successfully
   - Failure reporting works (test with empty input)

### Manual Verification
- Review all changed files for consistency and correctness
- Verify config YAML examples match actual field names
