# v1-spec.md

# Distributed Batch AI Compute Platform — V1 Specification

## 1. Overview

This project aims to build a platform that allows idle GPUs from researchers/labs to be pooled together and used for asynchronous AI workloads.

Users interact with the platform through a simple OpenAI-like dashboard/API. They submit batch AI jobs (primarily inference jobs initially), and the platform schedules these jobs on available provider GPUs.

GPU providers run a lightweight daemon on their Linux machines which:
- advertises available GPUs
- advertises hosted models
- receives compatible jobs
- executes workloads
- reports progress/results

The platform is intentionally:
- asynchronous
- batch-oriented
- centrally orchestrated
- not a general-purpose cloud platform

---

# 2. Goals of V1

The purpose of V1 is to validate:

- distributed worker orchestration
- scheduling over intermittent GPUs
- batch job execution
- provider registration
- model capability discovery
- usage accounting
- resumable workloads
- basic marketplace workflow

---

# 3. Explicit Non-Goals (Important)

V1 DOES NOT support:

- realtime chat APIs
- arbitrary user code execution
- SSH access
- virtual machines
- Kubernetes
- multi-node training
- distributed training
- arbitrary Docker uploads
- public model uploads
- advanced security isolation
- decentralized blockchain/token systems

Keep the system simple.

---

# 4. Core User Workflow

## User Flow

1. User logs into dashboard
2. User uploads JSONL batch prompts(containing prompt messages, model, generation parameters)
3. User submits batch job
4. Platform validate job
5. Platform queues workload
6. Compatible worker claims job
7. Worker executes batch inference
8. Results uploaded to platform storage
9. Platform notify the user(via email)
10. User downloads generated outputs

---

# 5. Supported Workloads in V1

V1 supports ONLY:

## 5.1 Batch Text Generation
Input:
- JSONL prompt file
Output:
- generated completions JSONL
Backend:
- vLLM
Example:
```json
{"custom_id":"992cf771154688b001a856c9f1166cde566e389f","method":"POST","url":"\/v1\/chat\/completions","body":{"model":"some-open-source-model","messages":[{"role":"user","content":"what is your name?"}],"max_tokens":1000}}
{"custom_id":"992cf771154688b001a856c9f1166cde566e389f","method":"POST","url":"\/v1\/chat\/completions","body":{"model":"some-open-source-model","messages":[{"role":"user","content":"where do you live?"}],"max_tokens":1000}}
```

## 5.2 Model identity — the catalogue (decision, 2026-07-18)

`body.model` is a **model catalogue id** (a stable platform slug), **not** a
free-form or raw runtime string. Users select from a curated catalogue
(`GET /v1/models`); validation rejects any id not in the catalogue.

- Each catalogue entry is **one pinned artifact** — weights + quantization +
  runtime. A quantized Ollama build and an fp16 HF build of "the same" model
  are **separate entries**, never merged, so a batch never silently swaps
  precision or runtime (reproducibility — *artifact*, not bit-exact).
- **Identity is curated; availability is derived from registrations.** The
  catalogue is not the union of what workers advertise (runtime tags float;
  worker metadata is untrusted). Workers feed availability, matched by
  **digest**; the tag alone never establishes identity.
- The raw runtime string lives only in `runtime_model_id` (internal); the
  scheduler hands it to the daemon at poll time. See §8.3 and
  `docs/model_catalogue.md`.
- **Onboarding** a new model = adding a pinned catalogue entry (curated /
  org-private / request→promote) — never an "run an uncatalogued model" path.

---

# 6. System Architecture

The system has 3 major components:

# 6.1 Control Plane (Central Server)

Responsible for:

- authentication
- job queue
- scheduling
- provider registry
- worker heartbeats
- usage tracking
- billing metadata
- API endpoints
- dashboard backend

Also responsible for:

- multi-tenant organization management (users, orgs, memberships, api keys)
- worker/runtime/model/GPU inventory (static + dynamic capability tracking)

**Suggested Stack:**

- FastAPI
- SQLite (embedded, per schema below — libSQL/Turso compatible for future scale-out)
- Redis
- Celery/RQ (optional)

---

# 6.2 Worker Daemon

Runs on provider machines.
Responsibilities:
- register worker
- advertise capabilities
- poll for jobs
- execute jobs
- upload outputs
- send heartbeats
- report GPU stats

Suggested Stack:
- Python initially
- Docker-based runtime
- NVIDIA Container Toolkit

---

# 6.3 Runtime Layer

Actual inference runtime.

Supported runtimes (decision 3.1, 2026-07-17 — amended from "vLLM ONLY"):

- Ollama (daemon default)
- vLLM

The daemon abstracts both behind a common executor interface
(`BaseExecutor` → `OllamaExecutor` / `VLLMExecutor`), selected by the
`runtime` config field; per-prompt timeout is the runtime-neutral
`inference_timeout`.

Do NOT support:
- ComfyUI
- custom runtimes
Those can come later.

---

# 7. High-Level Architecture

```text
+-------------------+
|   User Dashboard  |
+-------------------+
          |
          v
+-------------------+
|   Control Plane   |
|-------------------|
| Auth              |
| Scheduler         |
| Queue             |
| Billing           |
| Job Tracking      |
+-------------------+
          |
          v
+-------------------+
| Worker Registry   |
+-------------------+
          |
          v
+-------------------+
| Provider Daemon   |
|-------------------|
| Poll jobs         |
| Execute batches   |
| Upload outputs    |
+-------------------+
          |
          v
+-------------------+
| vLLM Runtime      |
+-------------------+
```

---

# 8. Users, Organizations & Worker Registration

## 8.0 Multi-Tenancy Model

All users are equal by default; every user is created with a personal
"Personal" organization. Users may create additional organizations and
invite other users into them.

- Any user can submit jobs.
- Any user can register workers.
- The user who creates an organization becomes its `owner`.
- Roles within an organization: `owner`, `admin`, `viewer`.
  - `owner`/`admin` can add members, edit worker availability settings.
  - `viewer` can see workers, jobs, and usage but cannot modify.
- There is **no separate provider signup/login** — a single user identity
  covers both "consumer" (submits jobs) and "provider" (hosts workers) roles.
  Every user gets access to two views:
  - **User portal** — submitted batches, usage, billing.
  - **Provider portal** — their org's workers, worker settings, jobs processed.
- Admin (platform operator) login is separate from regular user auth.

**Shared-machine rule:** a single physical machine may run only **one**
worker registration. If a GPU machine is shared by multiple people, those
people must be added to the owning organization (as `admin`/`viewer`)
rather than each registering a separate worker on the same box.

### API Keys

- Users create API keys **scoped to an organization** (not to themselves).
- Workers authenticate and register using an org's API key, which is how
  a worker becomes associated with — and visible to — that organization.
- Multiple API keys may exist per organization (e.g. one per lab machine
  or cluster).

```sql
CREATE TABLE users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    full_name TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    must_change_password INTEGER NOT NULL DEFAULT 0 CHECK (must_change_password IN (0, 1)),
    created_at INTEGER NOT NULL DEFAULT (unixepoch())
);

CREATE TABLE organizations (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    created_at INTEGER NOT NULL DEFAULT (unixepoch()),

    FOREIGN KEY (owner_id) REFERENCES users(id)
        ON DELETE CASCADE
);

CREATE TABLE organization_memberships (
    id          TEXT PRIMARY KEY DEFAULT ('mem-' || lower(hex(randomblob(12)))),
    org_id      TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role        TEXT NOT NULL CHECK (role IN ('owner', 'admin', 'viewer')),
    created_at  INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    updated_at  INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    UNIQUE(org_id, user_id)
);

CREATE INDEX idx_org_memberships_user   ON organization_memberships(user_id);
CREATE INDEX idx_org_memberships_org    ON organization_memberships(org_id);

CREATE TABLE api_keys (
    id                  TEXT PRIMARY KEY
                            DEFAULT ('key-' || lower(hex(randomblob(12)))),

    org_id              TEXT NOT NULL
                            REFERENCES organizations(id) ON DELETE CASCADE,

    created_by_user_id  TEXT NOT NULL
                            REFERENCES users(id) ON DELETE RESTRICT,

    name                TEXT NOT NULL,                  -- e.g. "GPU Server 1", "Lab Cluster"

    key_prefix          TEXT NOT NULL,                  -- first few chars shown in UI
    key_hash            TEXT NOT NULL UNIQUE,           -- SHA-256/Argon2 hash of full key

    status              TEXT NOT NULL DEFAULT 'active'
                            CHECK (status IN ('active','revoked')),

    last_used_at        INTEGER,
    expires_at          INTEGER,

    created_at          INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    revoked_at          INTEGER
);

CREATE INDEX idx_api_keys_org        ON api_keys(org_id);
CREATE INDEX idx_api_keys_creator    ON api_keys(created_by_user_id);
CREATE INDEX idx_api_keys_status     ON api_keys(status);
```

## 8.1 Worker Registration

A worker is a machine with resources, registered against an org via an
API key. Registration captures **static** properties; heartbeats update
**dynamic** properties (GPU VRAM available, RAM available, models loaded,
models available).

```sql
CREATE TABLE workers (
    id                    TEXT PRIMARY KEY
                              DEFAULT ('worker-' || lower(hex(randomblob(12)))),

    org_id                   TEXT NOT NULL,                 -- owning org; drives all access control
    api_key_id       TEXT NOT NULL,                 -- api key used at registration (audit trail, not access control)

    hostname                     TEXT NOT NULL,
    os                              TEXT,

    cpu_cores                         INTEGER,
    ram_total_gb                         REAL,

    supported_engines                       TEXT NOT NULL DEFAULT '[]',   -- JSON array, e.g. '["ollama","vllm"]'

    -- Liveness: managed SERVER-side (set online on heartbeat arrival,
    -- offline by the sweeper after a heartbeat timeout).
    status                                     TEXT NOT NULL DEFAULT 'online'
                                                  CHECK (status IN ('online','offline','draining','error')),


    -- Activity: what the daemon reports it is doing, carried in every
    -- heartbeat. Deliberately a separate vocabulary from liveness —
    -- a worker can be status='offline' with last-known activity='busy'.
    activity                                   TEXT NOT NULL DEFAULT 'idle'
                                                  CHECK (activity IN ('idle','busy','downloading_model')),

    -- Dynamic capability data (V1): written by every heartbeat, matched
    -- on by the scheduler. NULL until the first heartbeat arrives (poll
    -- then falls back to registration-advertised models).
    vram_total_gb                              REAL,
    vram_available_gb                          REAL,
    loaded_models                              TEXT,                        -- JSON array
    last_heartbeat                                 INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    created_at                                       INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);

CREATE INDEX idx_workers_org              ON workers(org_id);
CREATE INDEX idx_workers_api_key ON workers(api_key_id);
CREATE INDEX idx_workers_status           ON workers(status);
```

> **Implementation note (2026-07-17):** the normalized inventory tables
> below (§8.2 `worker_runtimes`, §8.3 `runtime_models`, §8.4
> `worker_gpus`) are **implemented** — the earlier V1 JSON-blob columns
> on `workers` (`runtimes`, `gpus`, `loaded_models`) were migrated into
> them and dropped (startup migration backfills pre-existing DBs).
> Implementation deltas from the DDL below: string PKs (`wrt-…`,
> `rtm-…`, `gpu-…`) and unix-int timestamps per repo convention;
> `runtime_models` gains a `loaded` boolean (in-VRAM now, updated by
> each heartbeat) alongside `status` (on-disk availability). Aggregate
> `vram_total_gb`/`vram_available_gb` stay on `workers` (§8.1) since
> the daemon reports machine totals, not per-GPU stats.

## 8.2 Worker Runtimes

Each worker exposes one or more runtimes (inference engines):

```sql
CREATE TABLE worker_runtimes (
    runtime_id              TEXT PRIMARY KEY,             -- uuid
    engine                  TEXT NOT NULL CHECK (engine IN ('ollama','vllm','tgi','transformers')),
    api_protocol            TEXT NOT NULL CHECK (api_protocol IN ('openai-compatible','ollama-native','custom')),

    base_url                TEXT NOT NULL,
    chat_path               TEXT,
    completions_path        TEXT,
    embeddings_path         TEXT,

    max_tokens              INTEGER,
    max_concurrent_requests INTEGER,
    request_timeout_seconds INTEGER NOT NULL DEFAULT 120,

    auth_type               TEXT NOT NULL DEFAULT 'none' CHECK (auth_type IN ('none','api_key','bearer')),
    secret_ref               TEXT,                         -- pointer into secrets manager, never raw secret

    status                   TEXT NOT NULL DEFAULT 'ready' CHECK (status IN ('ready','draining','unavailable')),
    last_health_check_at     TEXT,                          -- ISO8601

    created_at                TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at                TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
```

Note: this generalizes the V1 "vLLM ONLY" runtime assumption (§6.3) —
the schema is engine-agnostic (`ollama`, `vllm`, `tgi`, `transformers`)
even though V1 scheduling/execution only targets vLLM.

## 8.3 Runtime Models

> **Implementation note (2026-07-18):** `runtime_models` is the per-worker
> **availability** layer — kept lean (`name`, `runtime_model_id`, `digest`,
> `loaded`, `status`). The descriptive/curated metadata below
> (`task_type`, `parameter_count`, `quantization`, `context_length`,
> `size_bytes`) was moved to the **`model_catalog`** table (curated once,
> not replicated per worker). `body.model` is a `model_catalog` id;
> registrations feed availability, matched to catalogue entries by
> `digest`. See §5.2 and `docs/model_catalogue.md`.

Each runtime hosts one or more models with capability metadata used by
the scheduler for matching:

```sql
CREATE TABLE runtime_models (
    id                  TEXT PRIMARY KEY,             -- uuid
    runtime_id          TEXT NOT NULL REFERENCES worker_runtimes(runtime_id)
                            ON DELETE CASCADE,        -- owning runtime (fixed 2026-07-17: was missing)

    name                 TEXT NOT NULL,                 -- display name, e.g. 'llama3:8b' or 'mistralai/Mistral-7B-v0.1'

    runtime               TEXT NOT NULL CHECK (runtime IN ('ollama','vllm','tgi','transformers')),
    runtime_model_id       TEXT NOT NULL,                 -- exact id the provider API expects
    revision                TEXT,                          -- tag / commit hash / branch

    task_type                TEXT NOT NULL CHECK (task_type IN ('text-generation','embedding','chat','vision')),

    parameter_count            INTEGER,                       -- bigint-equivalent in SQLite
    quantization                 TEXT,                          -- q4_0, fp16, int8, etc.
    context_length                 INTEGER,
    size_bytes                       INTEGER,                       -- on-disk size

    license                            TEXT,
    local_path                           TEXT,                          -- where cached on disk

    status                                 TEXT NOT NULL DEFAULT 'not_downloaded'
                                             CHECK (status IN ('available','downloading','not_downloaded','error')),

    created_at                               TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at                                 TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    last_used_at                                 TEXT                            -- ISO8601, nullable
);

CREATE INDEX idx_models_runtime ON models(runtime);
CREATE INDEX idx_models_status  ON models(status);
CREATE INDEX idx_models_task_type ON models(task_type);
```

## 8.4 Worker GPUs

```sql
CREATE TABLE worker_gpus (
    id                    TEXT PRIMARY KEY,             -- uuid
    worker_id               TEXT NOT NULL REFERENCES workers(worker_id) ON DELETE CASCADE,

    gpu_index                 INTEGER NOT NULL,             -- 0, 1, 2... position on the machine

    -- static identity (set at registration)
    vendor                       TEXT CHECK (vendor IN ('nvidia','amd','intel','apple','other')),
    name                           TEXT,                          -- e.g. 'A100-80GB', 'RTX 4090', 'MI300X'
    vram_gb                          REAL,                          -- total VRAM, e.g. 80, 24, 8

    driver                              TEXT,                          -- driver version, e.g. '535.104.05'
    cuda                                  TEXT,                          -- CUDA version if NVIDIA, e.g. '12.2' (nullable otherwise)
    rocm                                    TEXT,                          -- ROCm version if AMD, e.g. '5.7' (nullable otherwise)

    -- dynamic (updated on heartbeat)
    updated_at                                    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE UNIQUE INDEX idx_worker_gpus_worker_index ON worker_gpus(worker_id, gpu_index);
CREATE INDEX idx_worker_gpus_worker ON worker_gpus(worker_id);
CREATE INDEX idx_worker_gpus_vendor ON worker_gpus(vendor);
```

---

# 9. Job Model

Example job object:

```json
{
  "job_id": "job-001",
  "input_file": "inputs.jsonl",
  "status": "queued"
}
```

---

# 10. Scheduling Logic (Simple V1)

Scheduler should:

- find online workers
- match requested model
- match available GPU
- assign queued jobs

Simple FIFO scheduling is acceptable initially.

No advanced optimization needed initially.

---

# 11. Pull-Based Worker Model

Workers should PULL jobs from server.

Reason:

- workers may be behind NAT
- workers may disconnect often
- easier fault tolerance

Example loop:

```text
worker:
    heartbeat()
    ask_for_job()
    execute()
    upload_results()
```

---

# 12. Fault Tolerance

Workers are intermittent.

Therefore:

- jobs must be resumable
- partial progress should be saved
- disconnected workers should timeout
- jobs should be requeued if worker disappears

Initial simplified approach:
- checkpoint after every N prompts

> **Status (2026-07-18):** worker timeout + requeue **are implemented**
> (sweeper marks a silent worker offline after 120s and requeues its
> in-flight batch; terminal failure after 3 attempts). Checkpointing /
> resume-from-partial is **NOT** implemented — a requeued batch restarts
> from prompt 0; progress is reported (`request_counts_*`) but partial
> results are not persisted. Tracked in [[Sheshnag - Batch resumability and checkpointing]].

---

# 13. Storage

Initial approach:
- local worker model cache
- centralized result storage

Suggested:
- MinIO or S3-compatible storage

Used for:
- uploaded prompt files
- generated outputs
- checkpoints

---

# 14. API Endpoints (Minimal)

## User APIs

The batch surface is **OpenAI-compatible** (files + batches), not a bare
`/jobs` resource — a batch references an uploaded input file and produces
an output file, all under `/v1`. Updated 2026-07-18 to match the
implementation.

### Upload Input File

```http
POST /v1/files          # multipart: file=<jsonl>, purpose="batch"
```

### Submit Batch

```http
POST /v1/batches        # { input_file_id, endpoint, completion_window }
```

Returns a batch at `status: "validating"`; async validation moves it to
`validated` (schedulable) or `failed`.

### Get Batch Status

```http
GET /v1/batches/{id}
GET /v1/batches                 # list caller's batches
GET /v1/batches/{id}/events     # SSE stream of validation status
```

### Download Outputs

```http
GET /v1/files/{output_file_id}/content   # output_file_id from the batch object
```

---

## Organization APIs

### Create Organization

```http
POST /orgs
```

### Add/Invite Member

```http
POST /orgs/{id}/members
```

### Create API Key (scoped to org)

```http
POST /orgs/{id}/api-keys
```

### Revoke API Key

```http
DELETE /orgs/{id}/api-keys/{key_id}
```

---

## Worker APIs

Worker requests are authenticated with an org-scoped API key
(see §8.0/8.1); the worker's `org_id` is derived from the key, not
supplied by the caller.

### Register Worker

```http
POST /workers/register
```

### Heartbeat

```http
POST /workers/{worker_id}/heartbeat
```

Body carries `activity` (`idle` | `busy` | `downloading_model`) plus the
dynamic capability fields (`vram_total_gb`, `vram_available_gb`,
`loaded_models`, GPU utilization, job progress). Liveness
(`status` = `online`/`offline`) is derived server-side from heartbeat
arrival and the sweeper timeout — the daemon never sets it.

### Poll Job

```http
POST /workers/poll
```

### Upload Results

```http
POST /workers/upload-results
```

---

# 15. Dashboard Requirements

Minimal dashboard should include a single login covering both roles a
user can have (consumer + provider), split into two portals per §8.0:

## User Portal (consumer side)

- login/signup
- organization switcher (Personal + any orgs the user belongs to)
- API key management (create/revoke, scoped to current org)
- member management (owner/admin only): invite, set role, remove
- submit batch job
- job history
- job status
- download outputs
- usage statistics

## Provider Portal (provider side)

- org's registered workers (status, hostname, last heartbeat)
- worker detail: GPUs, runtimes, models loaded/available
- worker availability settings (owner/admin only)
- jobs processed by the org's workers

---

## Platform Admin Side (separate login)

- online workers (cross-org)
- GPU inventory
- running jobs
- failed jobs
- token usage
- provider stats

---

# 16. Pricing Model (Simple)

Track:

- input tokens
- output tokens

Estimated cost:

```text
cost =
(input_tokens * input_rate) +
(output_tokens * output_rate)
```

Different models may have different pricing.

No payment gateway required initially.

Mock billing acceptable.

---

# 17. Security Assumptions

V1 assumes:
- trusted-enough academic providers
- controlled workloads only
- no arbitrary code execution

Still required:

- worker authentication via org-scoped API keys (hashed at rest, revocable)
- one worker registration per physical machine — shared machines are
  modeled as shared organization membership (admin/viewer), not multiple
  worker identities
- role-based access within an organization (owner/admin/viewer) gating
  who can view workers/jobs vs. who can edit worker settings
- signed API keys
- isolated inference runtime containers

---

# 18. Recommended Development Order

## Phase 1

- Control plane APIs
- SQLite schema (users, organizations, memberships, api_keys — §8.0)
- Worker registration + runtime/model/GPU schema (§8.1–8.4)
- Heartbeats

## Phase 2

- Job queue
- Batch upload
- Simple scheduler

## Phase 3

- Worker daemon
- vLLM integration
- Job execution

## Phase 4

- Result upload/download
- Dashboard UI

## Phase 5

- Retry handling
- Requeue logic
- Basic checkpointing

---

# 19. Suggested Tech Stack

## Backend

- FastAPI
- SQLite (see schema in §8) — libSQL/Turso-compatible for future scale-out
- Redis

## Worker

- Python
- Docker
- NVIDIA Container Toolkit

## Frontend

- Next.js or React

## Storage

- MinIO

## Runtime

- vLLM

---

# 20. Important Engineering Philosophy

Keep V1:

- boring
- simple
- debuggable
- observable

Avoid premature complexity.

Do NOT build:

- distributed systems research project
- generalized cloud platform
- decentralized protocol

The goal is:

- prove orchestration works
- prove idle GPU utilization works
- prove users submit useful workloads
- prove providers stay online enough

---

# 21. Success Criteria for V1

V1 is successful if:

- multiple workers can register
- workers can disconnect/reconnect
- users can submit jobs
- jobs complete successfully
- outputs are returned correctly
- scheduling works reliably
- token usage is tracked
- system survives worker failures

Nothing more is required initially.

---

# 22. Future Directions (NOT V1)

Possible future features:

- realtime inference APIs
- embeddings service
- LoRA fine-tuning
- model marketplace
- provider reputation system
- automatic GPU availability detection
- advanced scheduling
- autoscaling
- checkpoint-aware scheduling
- multi-GPU workloads
- distributed training
- institutional deployments

These are intentionally postponed.


---

I have domain gamekeepers.in as my organization web identity. I intend to host this project as `sheshnag.io` as moonknight domains are expensive.
Critique and suggest. 





---

## Architecture & Data Model

*(migrated from the project tracker)*

## Users  
All users are same and by default part of "Personal" Organization. They can create futher organizations and add other people to those organizations.

Any user can submit jobs. Any user can register their workers on platform.

User creates the organization and becomes the owner of it.

User can be owner/admin/member of organization.

Workers are registered by users as part of organization.

There is no separate provider signup/login. Only user login.

User can access both

a. user portal where they can see submitted batches, usage etc.

b. provider portal where they can see their workers, worker related settings, jobs processed by their workers etc.

Admin login is separate. 
Everyone else is a user as well as provider(based on workers registered)

---
## Workers  relationship with others
Multiple keys can be generated by a user as part of organization. 
User creates api-key. Api-key belongs to the organization.
Workers are registered and identified by that api-key so workers belongs to the organization and visible to anyone belonging to that organization.

Roles of user in organization:
- Owner
- admin
- viewer

A User(owner/admin) can add multiple people in an organization and they get to 
- see the resources(workers),  
- jobs processed by workers, 
- edit worker availability related settings.(owner/admin)
---
What happens if GPU machine is a shared resource? 
Are multiple workers per machine allowed?   
No. In that case people who shares the machine become part of the organization with admin/viewer access.

---
## Worker detailing
Worker is a machine with resources?
Worker has some static properties(shared via registration):
Hardware specs needed by scheduler 
See §8.1 for the authoritative `workers` DDL (static + dynamic columns).

Dynamic properties(shared via heartbeat):
GPU_vram available
RAM available
Models loaded,
Models available

**Workers have runtimes:**

```
CREATE TABLE worker_runtimes (
    runtime_id              TEXT PRIMARY KEY,             -- uuid
    engine                  TEXT NOT NULL CHECK (engine IN ('ollama','vllm','tgi','transformers')),
    api_protocol            TEXT NOT NULL CHECK (api_protocol IN ('openai-compatible','ollama-native','custom')),

    base_url                TEXT NOT NULL,
    chat_path               TEXT,
    completions_path        TEXT,
    embeddings_path         TEXT,

    max_tokens              INTEGER,
    max_concurrent_requests INTEGER,
    request_timeout_seconds INTEGER NOT NULL DEFAULT 120,

    auth_type               TEXT NOT NULL DEFAULT 'none' CHECK (auth_type IN ('none','api_key','bearer')),
    secret_ref               TEXT,                         -- pointer into secrets manager, never raw secret

    status                   TEXT NOT NULL DEFAULT 'ready' CHECK (status IN ('ready','draining','unavailable')),
    last_health_check_at     TEXT,                          -- ISO8601

    created_at                TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at                TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
```

**Runtimes have models**

Models have properties

```
CREATE TABLE runtime_models (
    id                  TEXT PRIMARY KEY,             -- uuid

    name                 TEXT NOT NULL,                 -- display name, e.g. 'llama3:8b' or 'mistralai/Mistral-7B-v0.1'

    runtime               TEXT NOT NULL CHECK (runtime IN ('ollama','huggingface')),
    runtime_model_id       TEXT NOT NULL,                 -- exact id the provider API expects
    revision                TEXT,                          -- tag / commit hash / branch

    task_type                TEXT NOT NULL CHECK (task_type IN ('text-generation','embedding','chat','vision')),

    parameter_count            INTEGER,                       -- bigint-equivalent in SQLite
    quantization                 TEXT,                          -- q4_0, fp16, int8, etc.
    context_length                 INTEGER,
    size_bytes                       INTEGER,                       -- on-disk size

    license                            TEXT,
    local_path                           TEXT,                          -- where cached on disk

    status                                 TEXT NOT NULL DEFAULT 'not_downloaded'
                                             CHECK (status IN ('available','downloading','not_downloaded','error')),

    created_at                               TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at                                 TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    last_used_at                                 TEXT                            -- ISO8601, nullable
);

CREATE INDEX idx_models_runtime ON models(runtime);
CREATE INDEX idx_models_status  ON models(status);
CREATE INDEX idx_models_task_type ON models(task_type);

```

**Workers have GPUs.**

GPUs have properties:
```sql
CREATE TABLE worker_gpus (
    id                    TEXT PRIMARY KEY,             -- uuid
    worker_id               TEXT NOT NULL REFERENCES workers(worker_id) ON DELETE CASCADE,

    gpu_index                 INTEGER NOT NULL,             -- 0, 1, 2... position on the machine

    -- static identity (set at registration)
    vendor                       TEXT CHECK (vendor IN ('nvidia','amd','intel','apple','other')),
    name                           TEXT,                          -- e.g. 'A100-80GB', 'RTX 4090', 'MI300X'
    vram_gb                          REAL,                          -- total VRAM, e.g. 80, 24, 8

    driver                              TEXT,                          -- driver version, e.g. '535.104.05'
    cuda                                  TEXT,                          -- CUDA version if NVIDIA, e.g. '12.2' (nullable otherwise)
    rocm                                    TEXT,                          -- ROCm version if AMD, e.g. '5.7' (nullable otherwise)

    -- dynamic (updated on heartbeat)
    updated_at                                    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE UNIQUE INDEX idx_worker_gpus_worker_index ON worker_gpus(worker_id, gpu_index);
CREATE INDEX idx_worker_gpus_worker ON worker_gpus(worker_id);
CREATE INDEX idx_worker_gpus_vendor ON worker_gpus(vendor);
```

---







 
### `users`

```
CREATE TABLE users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    full_name TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    must_change_password INTEGER NOT NULL DEFAULT 0 CHECK (must_change_password IN (0, 1)),
    created_at INTEGER NOT NULL DEFAULT (unixepoch())
);
```




### `organizations`

```
CREATE TABLE organizations (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    created_at INTEGER NOT NULL DEFAULT (unixepoch()),

    FOREIGN KEY (owner_id) REFERENCES users(id)
        ON DELETE CASCADE
);
```



### `organization_memberships`

```sql
CREATE TABLE organization_memberships (
    id          TEXT PRIMARY KEY DEFAULT ('mem-' || lower(hex(randomblob(12)))),
    org_id      TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role        TEXT NOT NULL CHECK (role IN ('owner', 'admin', 'viewer')),
    created_at  INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    updated_at  INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    UNIQUE(org_id, user_id)
);

CREATE INDEX idx_org_memberships_user   ON organization_memberships(user_id);
CREATE INDEX idx_org_memberships_org    ON organization_memberships(org_id);
```

### `api_keys`

```
CREATE TABLE api_keys (
    id                  TEXT PRIMARY KEY
                            DEFAULT ('key-' || lower(hex(randomblob(12)))),

    org_id              TEXT NOT NULL
                            REFERENCES organizations(id) ON DELETE CASCADE,

    created_by_user_id  TEXT NOT NULL
                            REFERENCES users(id) ON DELETE RESTRICT,

    name                TEXT NOT NULL,                  -- e.g. "GPU Server 1", "Lab Cluster"

    key_prefix          TEXT NOT NULL,                  -- first few chars shown in UI
    key_hash            TEXT NOT NULL UNIQUE,           -- SHA-256/Argon2 hash of full key

    status              TEXT NOT NULL DEFAULT 'active'
                            CHECK (status IN ('active','revoked')),

    last_used_at        INTEGER,
    expires_at          INTEGER,

    created_at          INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    revoked_at          INTEGER
);

CREATE INDEX idx_api_keys_org        ON api_keys(org_id);
CREATE INDEX idx_api_keys_creator    ON api_keys(created_by_user_id);
CREATE INDEX idx_api_keys_status     ON api_keys(status);
```
