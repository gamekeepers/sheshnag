# Ubiquitous Language

The vocabulary for talking about Sheshnag: who owns what, what gets submitted, what
runs it, and how a model becomes runnable. One term per concept; the aliases column
lists words to stop using for that concept.

## People and ownership

| Term | Definition | Aliases to avoid |
| --- | --- | --- |
| **User** | An authenticated person on a deployment, identified by one record regardless of what they do with it | Account, member account, provider account |
| **Organization** | The ownership and authorization boundary that holds API keys and workers | Org unit, team, lab, group, account |
| **Personal Organization** | The organization created for a user at signup, so they can register a worker or submit a batch without creating anything else | Personal account, default org |
| **Membership** | A user's `owner`, `admin` or `viewer` role inside one organization | Org permission, access level |
| **Platform role** | A user's global standing — `user` or `superadmin` — independent of any membership | Admin flag, global role |
| **Provider** | A person or organization lending GPU machines to a deployment | Worker owner, donor, contributor, host |
| **Operator** | The person or institution running a deployment | Admin, host, platform owner |
| **Deployment** | One self-hosted installation of Sheshnag — control plane, dashboard and the pool attached to it | Instance, environment, tenant, cluster |

## Credentials

| Term | Definition | Aliases to avoid |
| --- | --- | --- |
| **Personal API key** | An organization-independent key identifying a user for programmatic batch submission | User key, API token |
| **Org worker key** | An organization-owned key a daemon presents to register and to call every `/workers/*` endpoint | Worker token, daemon key, provider key, registration key |
| **JWT** | The dashboard's session credential, issued at login | Session token, bearer token, auth token |

## Submitted work

| Term | Definition | Aliases to avoid |
| --- | --- | --- |
| **Batch** | One submitted JSONL file of inference requests, the unit that is validated, scheduled, executed and completed | Job, task, run, submission |
| **Row** | One line of a batch — a single inference request carrying a `custom_id` | Prompt, request, record, item |
| **Input file** | The uploaded JSONL a batch is created from | Payload, upload, source file |
| **Output file** | The generated JSONL holding exactly one result row per input row, in input order | Results, response file |
| **Validation** | The asynchronous check of a batch's rows against the JSONL contract and the model catalogue, before it can be scheduled | Linting, parsing, preflight |
| **Assignment** | The record binding one batch to the one worker currently holding it | Lease, claim record, allocation |
| **Attempt** | One worker's execution of a batch; the third failed attempt makes the batch terminally `failed` | Retry, try, run |
| **Requeue** | Returning an `in_progress` batch to `validated` so another worker can claim it | Retry, reschedule, re-dispatch |

## Compute

| Term | Definition | Aliases to avoid |
| --- | --- | --- |
| **Worker** | One registered GPU machine, owned by an organization, that claims and executes batches | Node, box, machine, server, agent, provider |
| **Daemon** | The process running on a worker that polls, executes and heartbeats | Client, agent, runner |
| **Control plane** | The service that authenticates, validates, schedules, tracks and serves results, and owns the database | Backend, server, API, master |
| **Runtime** | One inference engine a worker exposes, such as Ollama or vLLM | Engine, executor, backend, server |
| **GPU** | One physical card on a worker, carrying its own VRAM, driver and vendor | Device, card, accelerator |
| **Pool** | Every online worker on a deployment, considered together | Fleet, cluster, grid, farm |
| **Capacity** | What a worker can be asked to run, expressed per card and per runtime rather than as a machine total | Resources, specs, size |
| **Fit rule** | A runtime's test of whether a worker can serve a given artifact, owned by the runtime rather than by the scheduler | Sizing check, VRAM check |
| **Heartbeat** | The daemon's periodic report of liveness, activity, VRAM and inventory | Ping, keepalive, status update |
| **Activity** | What a worker reports it is doing — `idle`, `busy` or `downloading_model` | Worker status, state |
| **Liveness** | A worker's server-decided `online` or `offline`, set by heartbeat recency | Worker status, health, availability |
| **Sweeper** | The control-plane loop that marks silent workers offline and requeues what they held | Reaper, watchdog, janitor |

## Models

| Term | Definition | Aliases to avoid |
| --- | --- | --- |
| **Artifact** | One concrete runnable thing — weights at a given quantization — identified by digest | Model, build, blob |
| **Catalogue entry** | One curated, pinned artifact users may select, with its scheduling requirement and provenance | Model, registry entry, catalogue model |
| **Catalogue id** | The stable platform slug a user puts in `body.model` | Model name, tag, model id |
| **Runtime model id** | The raw string a runtime knows an artifact by, such as an Ollama tag or a Hugging Face repo id | Model name, tag, model id |
| **Serving profile** | How one catalogue entry is served by one runtime, including every alternative name that entry answers to | Runtime mapping, model config |
| **Digest** | When present, the primary weights file's content hash, serving as the reproducibility pin and join key between a catalogue entry and a worker's copy; multi-file artifacts carry additional file hashes | Hash, checksum, sha, version |
| **Availability row** | A worker's record of holding one artifact, carrying its on-disk state and whether it is loaded | Worker model, installed model |
| **Loaded** | Resident in VRAM right now | Available, installed, downloaded, ready, active |
| **Present** | On the worker's disk, whether or not it is loaded | Available, downloaded, cached |
| **Reconciliation** | Classifying every artifact a worker reports against the catalogue as `available`, `unregistered`, `drift` or `missing` | Sync, matching, refresh |
| **Drift** | A worker's artifact claiming a pinned entry's name while its bytes match no pin under that name | Mismatch, stale model, conflict |
| **Quarantine** | The set of worker-reported hashes the scheduler will not route to | Blocklist, pending models, unknown models |
| **Adoption** | Promoting a quarantined hash into a catalogue entry, making rows holding it eligible once their runtime is schedulable | Approval, registration, whitelisting |
| **Self-heal** | Appending a name a worker uses to a catalogue entry's serving profile, once the digest has proved the artifact is the pinned one | Auto-fix, repair, rename |
| **Lineage** | The upstream base weights that group several quantizations of the same model, for display only | Family, parent model, base model |
| **Model picker** | The dashboard control where a user chooses a catalogue entry for a batch | Model list, model dropdown, model selector |

## Scheduling

| Term | Definition | Aliases to avoid |
| --- | --- | --- |
| **Scheduler** | The component that resolves a batch's catalogue entry and chooses an eligible worker for it | Picker, matcher, dispatcher, load balancer |
| **Poll** | A worker's request for a batch it can serve | Pull, fetch, dequeue |
| **Claim** | A worker taking ownership of a `validated` batch, moving it to `in_progress` | Lock, reserve, accept, assign |
| **Eligibility** | Whether a worker may be offered a batch at all — it hosts the artifact and satisfies the runtime's fit rule | Suitability, ranking, scoring |
| **Schedulable** | An availability row or runtime that may be given work — not quarantined, drifted or missing, and not on a draining or unreachable runtime | Active, enabled, ready, available |
| **Advertised** | Everything a worker's runtimes hold, whether or not it is schedulable | Available, offered, exposed |

## Relationships

- A **User** belongs to one or more **Organizations** through a **Membership**, and always to exactly one **Personal Organization**.
- An **Organization** owns zero or more **Org worker keys** and zero or more **Workers**; a **Personal API key** belongs to a **User**.
- A **Worker** belongs to exactly one **Organization**, has one or more **GPUs**, and exposes one or more **Runtimes**.
- A **Runtime** holds zero or more **Availability rows**, each naming one **Artifact** by **Digest**.
- A **Catalogue entry** pins exactly one **Artifact** and has one **Serving profile** per **Runtime** that can serve it.
- A **Batch** references exactly one **Catalogue entry**, has exactly one **Input file**, and produces at most one **Output file**.
- A **Batch** has at most one live **Assignment** and at most three **Attempts**.
- A **Batch** produces exactly one output **Row** per input **Row**, in input order.
- A **Worker** advertises every **Artifact** its **Runtimes** hold; only the **Schedulable** subset of those can be given a **Batch**.

## Example dialogue

> **Dev:** "A provider's worker shows the model in the dashboard but never gets offered the batch. Is the **Catalogue entry** wrong?"

> **Domain expert:** "What the dashboard lists is everything the worker **Advertises**, which is not the same set as what is **Schedulable**. Check the **Digest**: if the **Availability row** and the entry both carry one and they differ, that is **Drift**, and the **Scheduler** refuses it rather than running bytes nobody pinned."

> **Dev:** "And if the digest matches but the box calls the artifact something else?"

> **Domain expert:** "Then it **Self-heals** — **Reconciliation** appends that name to the entry's **Serving profile** for that **Runtime**, because the digest already proves the **Artifact**. The row becomes **Schedulable** on the next **Poll**, with no catalogue edit."

> **Dev:** "The worker has two 12 GB GPUs and the entry wants 20. Does the total carry it?"

> **Domain expert:** "No — **Capacity** is per card. Ollama's **Fit rule** needs one GPU large enough on its own, so that **Worker** is never **Eligible** for that entry no matter what the machine sums to."

> **Dev:** "Its heartbeat reports the model **Loaded**, though."

> **Domain expert:** "**Loaded** only means resident in VRAM. It never decides placement — **Eligibility** does, and a **Loaded** artifact is just the tie-breaker among the workers that already pass."

## Flagged ambiguities

- **"job" vs "batch"** — the daemon contract carries `job_id`, `current_job_id` and a `job` object, all holding a batch id. There is one scheduled unit and it is a **Batch**; keep `job` inside the wire contract's field names and say **Batch** everywhere else, including in prose about the daemon.
- **"provider"** — names both the person lending a GPU (**Provider**) and, in `auth_provider`, the identity source behind a login. Only the first is the domain term; note also that **Provider** is a role people play, never a record — the data model has users, organizations and workers.
- **"model"** — collapses **Catalogue entry** (curated identity), **Artifact** (the bytes), **Catalogue id** (what a user submits), **Runtime model id** (what a runtime is told to run), and **Availability row** (one worker's copy). Name which one every time; "the model" alone is never precise enough to schedule on.
- **"status"** — five different fields answer to it: a batch's lifecycle state, a worker's **Liveness**, a worker's **Activity**, an availability row's on-disk state, and a catalogue entry's curation state. Always qualify it.
- **"loaded" vs "available"** — **Loaded** means resident in VRAM, **Present** means on disk, and `available` is a reconciliation state meaning the row matches a catalogue pin. Three conditions, three words; a model can be `available` and **Present** without being **Loaded**.
- **"pool"** — names both the deployment's **Pool** of online workers and the daemon's bounded set of concurrent prompt slots. Reserve **Pool** for the workers and call the other one the **prompt concurrency pool**.
- **"VRAM total"** — a machine sum answers how large the **Pool** is; only the largest single GPU answers what one **Batch** can have. Never compare an entry's requirement against a machine total.
- **"picker"** — belongs to the interface, not to scheduling: the **Model picker**, the file picker, the Google account picker. The component that places a **Batch** on a **Worker** is the **Scheduler**.
- **"runtime" vs "executor"** — **Runtime** is the inference engine a worker exposes; the executor is the daemon class that drives one. Domain conversations only need **Runtime**.
