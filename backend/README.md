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

Two methods supported:

| Method | Header | Used By |
|---|---|---|
| JWT Token | `Authorization: Bearer eyJ...` | Frontend (after login) |
| API Key | `Authorization: Bearer gk-xxx` | Programmatic access |

---

## API Endpoints

### Auth — `/v1/auth`

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| `POST` | `/v1/auth/signup` | None | Register (user or provider) |
| `POST` | `/v1/auth/login` | None | Returns JWT token |
| `GET` | `/v1/auth/me` | Required | Get current user profile |
| `POST` | `/v1/auth/change-password` | Required | Change password |
| `POST` | `/v1/auth/api-keys/regenerate` | Required | Regenerate API key (users only) |

#### POST /v1/auth/signup
```json
// Request
{
  "email": "user@example.com",
  "password": "secret123",
  "full_name": "John Doe",
  "role": "user"          // "user" or "provider" (no admin signup)
}

// Response (200)
{
  "id": "user-abc123",
  "email": "user@example.com",
  "full_name": "John Doe",
  "role": "user",
  "api_key": "gk-xxxxxxxxxxxx",  // users only
  "is_active": true,
  "must_change_password": false,
  "created_at": 1780000000
}
```

#### POST /v1/auth/login
```json
// Request
{
  "email": "user@example.com",
  "password": "secret123"
}

// Response (200)
{
  "access_token": "eyJhbGciOi...",
  "token_type": "bearer",
  "must_change_password": false
}
```

#### POST /v1/auth/change-password
```json
// Request (requires auth)
{
  "old_password": "admin",
  "new_password": "new_secure_password"
}

// Response (200)
{"detail": "Password changed successfully"}
```

---

### Files — `/v1/files` (OpenAI-compatible)

| Method | Endpoint | Auth | Roles |
|---|---|---|---|
| `POST` | `/v1/files` | Required | user, admin |
| `GET` | `/v1/files/{file_id}/content` | Required | owner, admin |

#### POST /v1/files
```
Content-Type: multipart/form-data
Fields:
  - file: (binary) .jsonl file
  - purpose: "batch"
```
```json
// Response (200)
{
  "id": "file-abc123",
  "object": "file",
  "bytes": 1234,
  "created_at": 1780000000,
  "filename": "input.jsonl",
  "purpose": "batch"
}
```

#### GET /v1/files/{file_id}/content
Returns the raw file as a download.

---

### Batches — `/v1/batches` (OpenAI-compatible)

| Method | Endpoint | Auth | Roles |
|---|---|---|---|
| `POST` | `/v1/batches` | Required | user, admin |
| `GET` | `/v1/batches/{batch_id}` | Required | all (filtered) |
| `GET` | `/v1/batches` | Required | all (filtered) |

#### POST /v1/batches
```json
// Request
{
  "input_file_id": "file-abc123",
  "endpoint": "/v1/chat/completions",
  "completion_window": "24h"
}

// Response (200)
{
  "id": "batch-abc123",
  "object": "batch",
  "endpoint": "/v1/chat/completions",
  "model": "mistral-7b",       // extracted from JSONL
  "input_file_id": "file-abc123",
  "completion_window": "24h",
  "status": "validating",
  "output_file_id": null,
  "created_at": 1780000000,
  "completed_at": null,
  "request_counts": {
    "total": 100,
    "completed": 0,
    "failed": 0
  }
}
```

> **Note:** Model name is extracted from the first line of the JSONL (`body.model`).
> Unknown/unsupported models are rejected with `400`.

#### GET /v1/batches (role-based filtering)

| Role | Sees |
|---|---|
| **User** | Own batches only (full detail) |
| **Provider** | All batches — summary only (no `input_file_id`, no prompt content) |
| **Admin** | All batches (full detail) |

---

### Workers — `/workers` (Internal)

| Method | Endpoint | Auth | Roles |
|---|---|---|---|
| `POST` | `/workers/heartbeat` | Required | provider, admin |
| `POST` | `/workers/poll` | Required | provider, admin |
| `POST` | `/workers/upload-results` | Required | provider, admin |
| `POST` | `/workers/report-failure` | Required | provider, admin |

#### POST /workers/heartbeat
```json
// Request — provider sends machine specs periodically
{
  "worker_id": "gpu-box-01",
  "vram_total_gb": 24.0,
  "vram_available_gb": 18.5,
  "loaded_models": ["mistral-7b", "llama-3-8b"]
}

// Response (200)
{"status": "ok"}
```

#### POST /workers/poll
```json
// Request
{"worker_id": "gpu-box-01"}

// Response (200) — job found
{
  "job": {
    "job_id": "batch-abc123",
    "input_file_id": "file-abc123",
    "input_path": "/v1/files/file-abc123/content",
    "model": "mistral-7b"
  }
}

// Response (200) — no compatible job
{"job": null}
```

> **Smart Matching:** The picker filters by VRAM capacity and prefers
> providers that already have the model loaded.

#### POST /workers/upload-results
```
Content-Type: multipart/form-data
Fields:
  - job_id: "batch-abc123"
  - file: (binary) output .jsonl
```

#### POST /workers/report-failure
```json
{
  "job_id": "batch-abc123",
  "worker_id": "gpu-box-01",
  "error": "Out of memory"
}
```

---

## Database Schema

Using **SQLite** with SQLAlchemy ORM.

### Users
| Column | Type | Notes |
|---|---|---|
| id | String | PK, `user-{uuid}` |
| email | String | Unique |
| password_hash | String | Bcrypt |
| full_name | String | |
| role | String | admin / user / provider |
| api_key | String | `gk-{random}`, users only |
| is_active | Boolean | Default true |
| must_change_password | Boolean | Default false |
| created_at | Integer | Unix timestamp |

### Files
| Column | Type | Notes |
|---|---|---|
| id | String | PK, `file-{uuid}` |
| user_id | String | FK → who uploaded |
| filename | String | Original filename |
| purpose | String | batch / batch_output |
| bytes | Integer | File size |
| filepath | String | Local storage path |
| created_at | Integer | Unix timestamp |

### Batches
| Column | Type | Notes |
|---|---|---|
| id | String | PK, `batch-{uuid}` |
| user_id | String | FK → who submitted |
| endpoint | String | /v1/chat/completions |
| model | String | Extracted from JSONL |
| input_file_id | String | FK → files |
| status | String | validating → in_progress → completed/failed |
| output_file_id | String | FK → files (nullable) |
| request_counts_total | Integer | |
| request_counts_completed | Integer | |
| request_counts_failed | Integer | |

### Provider Capabilities
| Column | Type | Notes |
|---|---|---|
| worker_id | String | PK, machine identifier |
| provider_id | String | FK → users |
| vram_total_gb | Float | Total GPU VRAM |
| vram_available_gb | Float | Available VRAM |
| loaded_models | String | JSON array of model names |
| status | String | online / offline |
| last_heartbeat | Integer | Unix timestamp |

### Batch Assignments
| Column | Type | Notes |
|---|---|---|
| batch_id | String | PK, FK → batches |
| worker_id | String | Which worker took it |
| assigned_at | Integer | Unix timestamp |

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
validating → in_progress → completed
                         → failed
```

---

## Migration

Currently using SQLAlchemy `Base.metadata.create_all()` which auto-creates
tables on startup. **No formal migration tool (Alembic) is configured yet.**

For dev: delete `jobs.db` and restart the server to recreate tables with
new schema.

For production: Alembic should be added for proper schema migrations.
This is tracked as a future improvement.

---

## Project Structure

```
backend/
├── main.py               # App entry, CORS, default admin, router mounts
├── database.py            # SQLite engine + session
├── models.py              # SQLAlchemy models (User, File, Batch, etc.)
├── schemas.py             # Pydantic request/response models
├── auth.py                # JWT, password hashing, API keys, middleware
├── provider_picker.py     # Modular job-to-provider matching
├── requirements.txt       # Python dependencies
├── .gitignore             # Excludes DB, uploads, cache
└── routers/
    ├── __init__.py
    ├── auth.py            # /v1/auth/* endpoints
    ├── files.py           # /v1/files endpoints
    ├── batches.py         # /v1/batches endpoints
    └── workers.py         # /workers/* endpoints (internal)
```
