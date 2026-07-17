# Batch AI Compute Platform — Backend API

## Quick Start

```bash
cd backend
pip install -r requirements.txt
python -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

**Interactive API docs:** [http://localhost:8000/docs](http://localhost:8000/docs) (Swagger UI)

---

## Default Admin

Created automatically on first startup:
- **Email:** `admin@platform.com`
- **Password:** `admin`
- ⚠️ Must change password on first login (`must_change_password: true`)

---

## Authentication

Three credentials, strictly separated (spec §8.0/§17):

| Credential | Header | Used by | Accepted on |
|---|---|---|---|
| JWT token | `Authorization: Bearer eyJ...` | Frontend (after login) | `/v1/*` dashboard endpoints |
| **Personal** API key | `Authorization: Bearer gk-xxx` | Programmatic user access | `/v1/files`, `/v1/batches` (via `get_human_context`) |
| **Org worker** API key | `Authorization: Bearer gk-xxx` | Worker daemons | `/workers/*` only (via `get_worker_context`) |

There is no provider role and no per-user key column: every user gets a
**Personal Org** (with `owner` membership) at signup; worker keys belong to
organizations and are managed from the dashboard. The backend never issues
keys during worker registration — it derives the owning org from the key.

---

## API Endpoints

### Auth — `/v1/auth`

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| `POST` | `/v1/auth/signup` | None | Register; creates the user + their Personal Org |
| `POST` | `/v1/auth/login` | None | Returns JWT (`platform_role`, `must_change_password`) |
| `GET` | `/v1/auth/me` | JWT | Current user profile + org memberships |
| `POST` | `/v1/auth/change-password` | JWT | Change password |
| `POST` | `/v1/auth/api-keys/regenerate` | JWT/personal key | Regenerate the caller's personal API key |
| `POST` | `/v1/auth/forgot-password` | None | Email a reset token |
| `POST` | `/v1/auth/reset-password` | None | Redeem a reset token |
| `GET` | `/v1/admin/users` | superadmin | List all users |
| `GET` | `/v1/admin/workers` | superadmin | List all workers across orgs |
| `GET` | `/v1/admin/organizations` | superadmin | List all organizations |

#### POST /v1/auth/signup
```json
// Request — no role field; platform_role defaults to "user"
{
  "email": "user@example.com",
  "password": "secret123",
  "full_name": "John Doe"
}

// Response (200) — no api_key; keys are created separately
{
  "id": "user-abc123",
  "email": "user@example.com",
  "full_name": "John Doe",
  "platform_role": "user",
  "is_active": true,
  "must_change_password": false,
  "created_at": 1780000000
}
```

### Personal API keys — `/v1/users/me/api-keys`

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/v1/users/me/api-keys` | Create a personal key (raw key returned **once**) |
| `GET` | `/v1/users/me/api-keys` | List own keys (prefix only) |
| `PUT` | `/v1/users/me/api-keys/{key_id}` | Rename / set expiry / revoke |
| `DELETE` | `/v1/users/me/api-keys/{key_id}` | Revoke |

### Organizations — `/v1/orgs` (JWT)

| Method | Endpoint | Access | Description |
|---|---|---|---|
| `GET` | `/v1/orgs` | member | Orgs the caller belongs to |
| `GET` | `/v1/orgs/{org_id}/api-keys` | member | List org worker keys (prefix only) |
| `POST` | `/v1/orgs/{org_id}/api-keys/regenerate` | owner/admin | Rotate the org worker key |
| `GET` | `/v1/orgs/{org_id}/workers` | member | Org's workers incl. `status`, `activity`, live VRAM, loaded models |

### Files — `/v1/files` (OpenAI-compatible; JWT or personal key)

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/v1/files` | Upload a `.jsonl` batch input (multipart: `file`, `purpose="batch"`) |
| `GET` | `/v1/files/{file_id}/content` | Download raw file (also used by workers for job input) |

### Batches — `/v1/batches` (OpenAI-compatible; JWT or personal key)

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/v1/batches` | Create a batch; async JSONL validation kicks off |
| `GET` | `/v1/batches/{batch_id}/events` | SSE stream of validation events |
| `GET` | `/v1/batches/{batch_id}` | Batch detail (owner or superadmin) |
| `GET` | `/v1/batches` | `user` sees own batches; `superadmin` sees all |

> **Model handling:** the model name is extracted from the JSONL during
> validation (`body.model`, must be consistent across lines).
> ⚠️ Validation does **not** yet reject models outside the platform's
> supported list — enforcement is a tracked follow-up (see the
> `provider_picker.is_model_supported` helper, currently unused).

### Workers — `/workers` (org worker key required)

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/workers/register` | Register/re-register; backend assigns `worker_id` |
| `POST` | `/workers/{worker_id}/heartbeat` | Unified heartbeat: liveness + activity + dynamic capabilities |
| `POST` | `/workers/poll` | Claim the best-matching validated batch |
| `POST` | `/workers/progress` | Live prompt counts (every N prompts) |
| `POST` | `/workers/model-progress` | Model download progress (logged; liveness) |
| `POST` | `/workers/upload-results` | Upload output JSONL + real completed/failed counts |
| `POST` | `/workers/report-failure` | Report failure → batch is **requeued** (up to 3 attempts) |

All endpoints verify ownership: the worker must belong to the key's org,
and result/progress/failure reports must come from the worker the batch is
assigned to (403 otherwise).

#### POST /workers/{worker_id}/heartbeat
```json
// activity: idle | busy | downloading_model (validated).
// Liveness (workers.status online/offline) is server-managed.
{
  "activity": "busy",
  "current_job_id": "batch-abc123",
  "progress": {"total_prompts": 100, "completed_prompts": 40, "failed_prompts": 1},
  "gpu_utilization": 87.5,
  "gpu_memory_used_gb": 14.2,
  "vram_total_gb": 24.0,
  "vram_available_gb": 9.8,
  "loaded_models": ["mistral-7b"],
  "uptime_seconds": 3600
}
```

#### POST /workers/poll
```json
// Request
{"worker_id": "worker-abc123"}

// Response — job found
{
  "job": {
    "job_id": "batch-abc123",
    "input_file_id": "file-abc123",
    "input_path": "/v1/files/file-abc123/content",
    "model": "mistral-7b"
  }
}

// Response — nothing compatible
{"job": null}
```

> **Matching:** the picker filters by VRAM (from heartbeats) and prefers
> workers that already have the model loaded. A worker that has never
> heartbeated only receives batches whose model it advertised at
> registration — never an arbitrary batch.

#### POST /workers/upload-results
```
Content-Type: multipart/form-data
Fields:
  - job_id:    "batch-abc123"
  - worker_id: "worker-abc123"     (must hold the assignment)
  - completed: 98                  (optional — real success count)
  - failed:    2                   (optional — real failure count)
  - file:      (binary) output .jsonl
```

#### POST /workers/report-failure
```json
// Request
{"job_id": "batch-abc123", "worker_id": "worker-abc123", "error": "OOM"}

// Response — requeued until attempts hit the max (3), then terminal
{"status": "validated", "batch_id": "batch-abc123", "attempts": 1, "error": "OOM"}
```

---

## Fault Tolerance (spec §12)

- `report-failure` requeues the batch (`status` back to `validated`,
  `attempts += 1`, assignment voided); after **3** attempts it is marked
  `failed` terminally.
- A background **sweeper** (started at app startup, 60s interval) marks
  workers `offline` after **120s** without a heartbeat and requeues their
  in-flight batches — a crashed daemon never strands a batch.

See `sweeper.py` for the thresholds.

---

## Database Schema

SQLite with SQLAlchemy ORM. Tables (see `models.py`):

| Table | Purpose / notable columns |
|---|---|
| `users` | `platform_role` (`user`/`superadmin`), `must_change_password` — no role/api_key columns |
| `organizations` | Ownership boundary; owner derived from memberships |
| `organization_memberships` | `role`: `owner` / `admin` / `viewer` |
| `api_keys` | Hashed keys; `key_type`: `worker` (org-scoped) or `personal`; prefix for UI |
| `workers` | Static specs; `status` (liveness, server-managed) vs `activity` (daemon-reported); aggregate `vram_total_gb` / `vram_available_gb` from heartbeats |
| `worker_runtimes` | Inference engines a worker exposes (spec §8.2): engine, base_url, status |
| `runtime_models` | Models per runtime (spec §8.3): `status` (on-disk) + `loaded` (in VRAM, heartbeat-updated) |
| `worker_gpus` | Physical GPUs per worker (spec §8.4): vendor, name, vram_gb, driver, cuda |
| `files` | Uploaded inputs and generated outputs |
| `batches` | Lifecycle status, request counts, `attempts` (requeue counter) |
| `batch_assignments` | Which worker holds which batch (FK → `workers.id`) |
| `password_reset_tokens` | Forgot-password flow |

> Inventory is fully normalized per spec §8.2–8.4. Pre-existing DBs that
> used the old JSON columns on `workers` are backfilled automatically at
> startup (`migrations.py`), which also drops the legacy columns and the
> orphaned `provider_capabilities` table.

---

## Supported Models (VRAM Requirements)

| Model | VRAM Required |
|---|---|
| mistral-7b | 16 GB |
| mistral-7b-instruct | 16 GB |
| llama-3-8b | 18 GB |
| llama-3.1-8b | 18 GB |
| llama-3-70b | 80 GB |
| qwen2-7b | 16 GB |

> Edit `provider_picker.py` → `MODEL_VRAM_REQUIREMENTS` to add/change models.

---

## Batch Status Lifecycle

```
validating → validated → in_progress → completed
     ↓            ↑            ↓
   failed         └────────────┘ requeue (failure/offline worker,
                                 max 3 attempts) → failed
```

---

## Migration

`Base.metadata.create_all()` creates tables on startup, then
`migrations.py` runs idempotent startup migrations: column guards for
columns added after a table shipped (e.g. `batches.attempts`), the
JSON→rows inventory backfill, legacy column drops, and removal of the
orphaned `provider_capabilities` table. **No formal migration tool
(Alembic) yet** — for dev, deleting `jobs.db` and restarting also works.

---

## Project Structure

```
backend/
├── main.py                # App entry, CORS, column guard, sweeper + admin startup
├── database.py            # SQLite engine + session
├── models.py              # SQLAlchemy models
├── schemas.py             # Pydantic request/response models
├── auth.py                # JWT, hashing, key contexts (worker/personal/human)
├── provider_picker.py     # Job-to-worker matching (VRAM + loaded models)
├── migrations.py          # Idempotent startup migrations (backfills, column guards)
├── sweeper.py             # Requeue logic + stale-worker sweeper (spec §12)
├── rate_limit.py          # Key-creation rate limiting
├── requirements.txt
├── routers/
│   ├── auth.py            # /v1/auth/*, /v1/admin/*
│   ├── users.py           # /v1/users/me/api-keys (personal keys)
│   ├── organizations.py   # /v1/orgs/* (org keys, org workers)
│   ├── files.py           # /v1/files
│   ├── batches.py         # /v1/batches (+ SSE validation events)
│   └── workers.py         # /workers/* (daemon-facing)
└── services/
    ├── batch_validator.py # Async JSONL validation
    ├── sse_manager.py     # Validation event stream
    └── email_service.py   # Password-reset mail
```
