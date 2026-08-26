# Database Design Rationale

> **Implementation note (2026-07-17):** the Worker Runtimes / Runtime
> Models / Worker GPUs entities described below are **implemented** as
> the `worker_runtimes`, `runtime_models`, and `worker_gpus` tables
> (`backend/models.py`), which supersede the earlier V1 JSON columns on
> `workers`. This document matches the implementation.

## Design Principles

The platform is built around a few simple principles:

1. **Every person is a user.**
2. **Organizations are the security boundary.**
3. **Workers belong to organizations, not individuals.**
4. **API keys identify workers, not users.**
5. **Platform administration is separate from organization administration.**

This model keeps the authorization rules simple while allowing collaboration between multiple users managing shared compute resources.

---

# Users

Every authenticated person in the system is represented by a single **User** record.

There are no separate concepts such as:

* Provider
* Worker owner
* Organization account

A user can simultaneously:

* submit AI jobs,
* register compute workers,
* create organizations,
* manage workers,
* collaborate with other users.

The only platform-level distinction is the **platform role** (`user`, `superadmin`), which determines global permissions.

Organization-specific permissions are handled separately through memberships.

---

# Organizations

Organizations are the primary ownership and authorization boundary within the platform.

Every user automatically belongs to a **Personal** organization. This allows an individual to immediately register workers and submit jobs without creating additional entities.

Users may also create additional organizations for teams, research groups or laboratories.

An organization owns:

* API keys
* Workers
* (future) Jobs
* (future) Batches
* (future) Usage information
* (future) Billing

Because ownership is organization-centric, resources remain accessible even if individual users leave the organization.

---

# Organization Memberships

Users may belong to multiple organizations.

Memberships define what a user is allowed to do within a particular organization.

Current roles are:

* **Owner**

  * Full control
  * Can manage members
  * Can delete the organization

* **Admin**

  * Can manage workers
  * Can create API keys
  * Can modify organization resources

* **Viewer**

  * Read-only access to organization resources

This allows laboratories and research groups to collaboratively manage shared infrastructure.

---

# Platform Roles

Platform roles are independent from organization roles.

A user may have:

* Platform Role = `superadmin`
* Organization Role = `viewer`

or

* Platform Role = `user`
* Organization Role = `owner`

The platform role determines global access.

Examples:

* Superadmin can view every worker on the platform.
* Auditor can inspect all resources without necessarily belonging to every organization.
* Regular users only see resources belonging to organizations where they are members.

This separation avoids the need to add administrators as members of every organization.

---

# API Keys

API keys belong to organizations rather than users.

A user creates an API key on behalf of an organization.

The key is then used exclusively for machine authentication.

The API key is **not** the authorization boundary.

Instead, it serves as an identity that allows a worker to register itself.

Once registration succeeds, the worker inherits the organization associated with that API key.

This allows:

* key rotation,
* revocation,
* auditing,
* multiple keys per organization,
* different keys for different clusters.

Only hashed keys are stored in the database.

---

# Workers

A worker represents a single machine capable of serving AI models.

Workers belong to organizations.

This is intentional because compute infrastructure is frequently shared between multiple users.

Instead of allowing multiple users to own the same worker, users become members of the organization that owns the worker.

This greatly simplifies permissions.

Any member of the organization can:

* view worker health,
* inspect loaded models,
* review processed jobs.

Organization owners and administrators may additionally:

* enable or disable workers,
* modify scheduling settings,
* manage runtimes.

---

# Shared Machines

GPU servers are often shared between researchers.

Instead of allowing multiple ownership of workers, the design treats the machine as an organization resource.

For example:

```
AI Lab
├── Alice (Owner)
├── Bob (Admin)
├── Carol (Viewer)
└── Worker: gpu-server-01
```

Everyone who needs access becomes a member of the organization.

This keeps ownership simple while avoiding duplicate worker registrations.

---

# Worker Registration Flow

Worker registration follows the sequence below.

```
User
 │
 │ Creates API Key
 ▼
Organization
 │
 │ owns
 ▼
API Key
 │
 │ used during registration
 ▼
Worker
```

During registration:

1. The worker presents an API key.
2. The backend validates the key.
3. The associated organization is identified.
4. The worker is permanently associated with that organization.

Subsequent heartbeats continue using the same API key for authentication.

---

# Worker Composition

A worker is composed of several related entities.

## Worker

Represents the physical machine.

Stores relatively static information such as:

* hostname,
* operating system,
* CPU,
* RAM,
* supported inference engines.

Dynamic values such as available RAM and heartbeat status are updated over time.

---

## Worker Runtimes

A worker may expose multiple inference runtimes.

Examples include:

* Ollama
* vLLM
* TGI
* Transformers

Each runtime defines:

* communication protocol,
* API endpoints,
* authentication method,
* health status,
* request limits.

This separation allows a single machine to simultaneously expose multiple inference servers.

---

## Runtime Models

Each runtime exposes one or more models.

Examples:

* llama3
* mistral
* nomic-embed
* Qwen

Model information includes:

* runtime identifier,
* task type,
* context length,
* quantization,
* model size,
* download status.

Keeping models separate from workers makes scheduling significantly easier.

Schedulers can answer questions such as:

* Which workers already have this model loaded?
* Which workers have the model downloaded?
* Which workers are capable of downloading the model?

---

## Worker GPUs

Workers may contain one or more GPUs.

Each GPU stores static hardware information such as:

* vendor,
* model,
* VRAM,
* driver,
* CUDA/ROCm version.

Representing GPUs separately allows the scheduler to make placement decisions based on individual GPU capabilities rather than treating an entire machine as homogeneous hardware.

---

# Overall Ownership Hierarchy

The resulting ownership hierarchy is intentionally straightforward.

```
Platform
│
├── Users
│
├── Organizations
│     │
│     ├── Members
│     ├── API Keys
│     └── Workers
│             │
│             ├── GPUs
│             └── Runtimes
│                     │
│                     └── Models
│
└── Superadmin
```

---

# Benefits of the Design

This design provides several advantages:

* A single identity model for every user.
* Clear separation between platform-wide and organization-specific permissions.
* Organizations own resources rather than individual users.
* Shared compute infrastructure is naturally supported.
* Worker authentication is independent from authorization.
* API keys can be rotated without affecting organization ownership.
* The scheduler has direct access to worker, runtime, model and GPU capabilities.
* The schema scales naturally as additional entities such as jobs, batches, usage tracking and billing are introduced.
