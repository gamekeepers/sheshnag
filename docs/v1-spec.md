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
2. User uploads JSONL batch prompts
3. User selects:
   - model
   - generation parameters
4. User submits batch job
5. Platform queues workload
6. Compatible worker claims job
7. Worker executes batch inference
8. Results uploaded to platform storage
9. User downloads generated outputs

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
```jsonl
{"custom_id":"unique_id","method":"POST","url":"/v1/chat/completions","body":{"model":"some-open-source-model","messages":[{"role":"user","content":"What is your name?"}],"max_tokens":1000}}
{"custom_id":"unique_id","method":"POST","url":"/v1/chat/completions","body":{"model":"some-open-source-model","messages":[{"role":"user","content":"Where do you live?"}],"max_tokens":1000}}
```

---

# 6. System Architecture

The system has 3 major components.

---

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

Suggested Stack:

- FastAPI
- PostgreSQL
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

Initial runtime:

- vLLM ONLY

Do NOT support:

- Ollama
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

# 8. Worker Registration

Workers register with metadata like:

```json
{
  "worker_id": "worker-123",
  "gpu_name": "RTX 4090",
  "vram_gb": 24,
  "models": [
    "Qwen2.5-32B-AWQ"
  ],
  "runtime": "vllm",
  "status": "online"
}
```

---

# 9. Job Model

Example job object:

```json
{
  "job_id": "job-001",
  "model": "Qwen2.5-32B-AWQ",
  "input_file": "inputs.jsonl",
  "status": "queued",
  "max_tokens": 512,
  "temperature": 0.7
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

### Submit Job

```http
POST /jobs
```

### Get Job Status

```http
GET /jobs/{id}
```

### Download Outputs

```http
GET /jobs/{id}/outputs
```

---

## Worker APIs

### Register Worker

```http
POST /workers/register
```

### Heartbeat

```http
POST /workers/heartbeat
```

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

Minimal dashboard should include:

## User Side

- login/signup
- API key page
- submit batch job
- job history
- job status
- download outputs
- usage statistics

---

## Admin Side

- online workers
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

- worker authentication
- signed API keys
- isolated inference runtime containers

---

# 18. Recommended Development Order

## Phase 1

- Control plane APIs
- PostgreSQL schema
- Worker registration
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
- PostgreSQL
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
