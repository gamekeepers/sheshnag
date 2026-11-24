# Ubiquitous Language

The vocabulary for talking about Sheshnag: who owns what, what gets submitted, what
runs it, how a model becomes runnable, and how a base model is trained into an
adapter. One term per concept; the aliases column lists words to stop using for
that concept.

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
| **Org worker key** | An organization-owned key a daemon or runner presents to register and to call every `/workers/*` endpoint | Worker token, daemon key, provider key, registration key |
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
| **Attempt** | A counted execution of a batch; the third one makes the batch terminally `failed`. A hand-back that journalled more than any before it, and a graceful stop, are not counted | Retry, try, run |
| **Requeue** | Returning an `in_progress` batch to `validated` so a worker can claim it again, which may be the worker that already holds its journal | Retry, reschedule, re-dispatch |
| **Hand-back** | A worker returning a batch it holds, reporting how many rows it has journalled | Release, give up, abort |
| **Journal** | The worker-local file of finished rows for one batch, keyed on each row's `custom_id` | Checkpoint, cache, partial output, log |
| **Resume** | Re-running a batch on the worker holding its journal, executing only the rows the journal has no result for | Restart, retry, continue |
| **Final** | A result a resume keeps rather than running the row again — a success, or a failure the row itself causes | Terminal, permanent, cached |
| **Graceful stop** | A provider stopping their own daemon: running prompts get the grace period, the batch is handed back, and no attempt is counted | Shutdown, drain, pause |
| **Usage record** | One row's token counts, keyed on `(batch, custom_id)` so a replayed upload cannot double-count | Token log, metering row, usage row |

## Compute

| Term | Definition | Aliases to avoid |
| --- | --- | --- |
| **Worker** | One registered GPU machine, owned by an organization, that claims and executes batches or finetune jobs | Node, box, machine, server, agent, provider |
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
| **Sweeper** | The control-plane loop that marks silent workers offline, requeues the batches they held, fails the finetune jobs whose runner went silent, and retries unfinished usage ingestion | Reaper, watchdog, janitor |
| **Provider policy** | The constraints a provider sets on their own worker — its availability window and its batch-size limit | Worker settings, limits, quota |
| **Availability window** | The provider's local-time hours in which their worker may be given a new batch; one already running is not stopped when the window closes | Schedule, uptime, hours, shift |
| **Batch-size limit** | The most rows a provider will accept in one batch on their worker | Max batch, cap, quota |

## Models

| Term | Definition | Aliases to avoid |
| --- | --- | --- |
| **Artifact** | One concrete runnable thing — weights at a given quantization — identified by digest | Model, build, blob |
| **Catalogue entry** | One curated, pinned artifact users may select, with its scheduling requirement and provenance | Model, registry entry, catalogue model |
| **Catalogue id** | The stable platform slug a user puts in `body.model` | Model name, tag, model id |
| **Runtime model id** | The raw string a runtime knows an artifact by, such as an Ollama tag or a Hugging Face repo id | Model name, tag, model id |
| **Serving profile** | How one catalogue entry is served by one runtime, including every alternative name that entry answers to | Runtime mapping, model config |
| **Digest** | When present, the primary weights file's content hash, serving as the reproducibility pin and join key between a catalogue entry and a worker's copy; multi-file artifacts carry additional file hashes | Hash, checksum, sha, version |
| **Footprint line** | A catalogue entry's weights size and per-token KV cost, derived from the pinned artifact's header, from which its requirement at any context is computed | VRAM estimate, size, memory profile |
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

## Finetuning

| Term | Definition | Aliases to avoid |
| --- | --- | --- |
| **Finetune job** | One submitted request to train a LoRA adapter on one pinned base from one training file, the unit that is validated, queued, trained and finished | Batch, training run, task |
| **Training file** | The uploaded chat JSONL (`purpose="fine-tune"`) a finetune job trains on; an optional **Validation file** is scored for eval loss | Dataset, input file, training data |
| **Job org** | The organization a finetune job belongs to — the one the caller named, or their only one — and the only organization whose runners are offered it | Owner org, target org |
| **Pinned base** | The catalogue entry a finetune job trains on, snapshotted at create as its source ref plus a commit-SHA revision | Base model, parent model, lineage |
| **Trainable base** | A catalogue entry with a SHA-pinned Hugging Face revision and a chat template in `finetune_contract.CHAT_TEMPLATES`; every other base is refused at create | Supported model, finetunable model |
| **Chat template** | The training engine's name for how a base formats a conversation, sent to the runner in the poll payload | Prompt format, template |
| **Runner** | forge-runner: the process on a worker that claims finetune jobs, supervises the training engine and uploads the adapter | Daemon, trainer, agent |
| **Training engine** | LLaMA-Factory, pinned by image digest, run by the runner as a supervised container | Runtime, engine, backend |
| **Finetune event** | One append-only entry in a finetune job's log; each trainer log step becomes one `metrics` event | Log line, progress row |
| **Adapter** | The trained LoRA weights a successful finetune job produces | Model, checkpoint, finetuned model |
| **Finetune artifact** | The record of one uploaded adapter, read from its own `adapter_config.json`, carrying the servability verdict | Artifact, output, result |
| **Servable** | An adapter vLLM can load under the platform's serving limits — LoRA, rank ≤ 64, the proven target modules, nothing in `modules_to_save` | Valid, deployable, ready |
| **Fine-tuned model** | The serving name of a job's adapter, `ft:<base>:<suffix>:<id>` | Model id, adapter name |
| **Settle** | What a poll does to a job its worker still holds: requeue it if no step was logged, fail it with `runner_restarted` if one was, drop the assignment if it already ended | Recover, clean up, reset |
| **Runner lost** | The failure of a `running` finetune job whose runner stopped heartbeating for 10 minutes; the user resubmits | Timeout, stale job |

## Scheduling

| Term | Definition | Aliases to avoid |
| --- | --- | --- |
| **Scheduler** | The component that resolves a batch's catalogue entry and matches it to an eligible polling worker | Picker, matcher, dispatcher, load balancer |
| **Poll** | A worker's request for a batch it can serve | Pull, fetch, dequeue |
| **Claim** | A worker taking ownership of a `validated` batch, moving it to `in_progress` | Lock, reserve, accept, assign |
| **Eligibility** | Whether a worker may be offered a batch at all — it hosts the artifact and satisfies the runtime's fit rule | Suitability, ranking, scoring |
| **Schedulable** | An availability row or runtime that may be given work — not quarantined, drifted or missing, and not on a draining or unreachable runtime | Active, enabled, ready, available |
| **Advertised** | Everything a worker's runtimes hold, whether or not it is schedulable | Available, offered, exposed |
| **Context** | The token window a batch is fitted and run at, taken from the p99 of its rows' needs | Window, length, size, tokens |
| **KV alpha** | One runtime's multiplier on the KV cache estimate, calibrated from what it reports loaded | Overhead, fudge factor, margin |

## Relationships

- A **User** belongs to one or more **Organizations** through a **Membership**, and always to exactly one **Personal Organization**.
- An **Organization** owns zero or more **Org worker keys** and zero or more **Workers**; a **Personal API key** belongs to a **User**.
- A **Worker** belongs to exactly one **Organization**, has one or more **GPUs**, and exposes one or more **Runtimes**.
- A **Runtime** holds zero or more **Availability rows**, each naming one **Artifact** by **Digest**.
- A **Catalogue entry** pins exactly one **Artifact** and has one **Serving profile** per **Runtime** that can serve it.
- A **Batch** references exactly one **Catalogue entry**, has exactly one **Input file**, and produces at most one **Output file**.
- A **Batch** has at most one live **Assignment** and at most three counted **Attempts**; a **Hand-back** that made progress and a **Graceful stop** add neither.
- A **Journal** belongs to one **Batch** on one **Worker**, and only that worker can **Resume** from it.
- A **Batch** carries one **Usage record** per executed **Row**.
- A **Batch** produces exactly one output **Row** per input **Row**, in input order.
- A **Worker** advertises every **Artifact** its **Runtimes** hold; only the **Schedulable** subset of those can be given a **Batch**.
- A **Finetune job** has exactly one **Job org**, one **Pinned base** (a **Trainable base**) and one **Training file**, and at most three **Attempts**.
- A **Finetune job** is offered only to **Runners** on **Workers** of its **Job org**, and has at most one live **Assignment**.
- A succeeded **Finetune job** produces one **Adapter**, recorded as a **Finetune artifact**; only a **Servable** one ends the job `succeeded`.

## Example dialogue

> **Dev:** "A provider's worker shows the model in the dashboard but never gets offered the batch. Is the **Catalogue entry** wrong?"

> **Domain expert:** "What the dashboard lists is everything the worker **Advertises**, which is not the same set as what is **Schedulable**. Check the **Digest**: if the **Availability row** and the entry both carry one and they differ, that is **Drift**, and the **Scheduler** refuses it rather than running bytes nobody pinned."

> **Dev:** "And if the digest matches but the box calls the artifact something else?"

> **Domain expert:** "Then it **Self-heals** — **Reconciliation** appends that name to the entry's **Serving profile** for that **Runtime**, because the digest already proves the **Artifact**. The row becomes **Schedulable** on the next **Poll**, with no catalogue edit."

> **Dev:** "The worker has two 12 GB GPUs and the entry wants 20. Does the total carry it?"

> **Domain expert:** "No — **Capacity** is per card. Ollama's **Fit rule** needs one GPU large enough on its own, so that **Worker** is never **Eligible** for that entry no matter what the machine sums to."

> **Dev:** "Its heartbeat reports the model **Loaded**, though."

> **Domain expert:** "**Loaded** only means resident in VRAM. It never decides placement — **Eligibility** does, and a **Loaded** artifact is just the tie-breaker among the workers that already pass."

> **Dev:** "A runner was restarted mid-training and its job still says `running`. Does the **Sweeper** pick it up?"

> **Domain expert:** "Not for 10 minutes, and by then the restarted **Runner** is heartbeating under the same worker id. Its next **Poll** **Settles** the held job: requeued if no step was logged, failed with `runner_restarted` if one was. Training is never restarted from step 0 behind the user's back."

> **Dev:** "Could a runner from another provider have claimed it?"

> **Domain expert:** "No — a **Finetune job** is offered only to workers of its **Job org**, so its **Training file** never leaves that organization's machines."

## Flagged ambiguities

- **"job" vs "batch"** — the daemon contract carries `job_id`, `current_job_id` and a `job` object, all holding a batch id; the runner contract's `job_id` holds a finetune job id. Say **Batch** for inference work and **Finetune job** for training; "job" alone is reserved for the wire contracts' field names.
- **"provider"** — names both the person lending a GPU (**Provider**) and, in `auth_provider`, the identity source behind a login. Only the first is the domain term; note also that **Provider** is a role people play, never a record — the data model has users, organizations and workers.
- **"model"** — collapses **Catalogue entry** (curated identity), **Artifact** (the bytes), **Catalogue id** (what a user submits), **Runtime model id** (what a runtime is told to run), and **Availability row** (one worker's copy). Name which one every time; "the model" alone is never precise enough to schedule on.
- **"status"** — five different fields answer to it: a batch's lifecycle state, a worker's **Liveness**, a worker's **Activity**, an availability row's on-disk state, and a catalogue entry's curation state. Always qualify it.
- **"loaded" vs "available"** — **Loaded** means resident in VRAM, **Present** means on disk, and `available` is a reconciliation state meaning the row matches a catalogue pin. Three conditions, three words; a model can be `available` and **Present** without being **Loaded**.
- **"pool"** — names both the deployment's **Pool** of online workers and the daemon's bounded set of concurrent prompt slots. Reserve **Pool** for the workers and call the other one the **prompt concurrency pool**.
- **"VRAM total"** — a machine sum answers how large the **Pool** is; only the largest single GPU answers what one **Batch** can have. Never compare an entry's requirement against a machine total.
- **"picker"** — belongs to the interface, not to scheduling: the **Model picker**, the file picker, the Google account picker. The component that places a **Batch** on a **Worker** is the **Scheduler**.
- **"runtime" vs "executor"** — **Runtime** is the inference engine a worker exposes; the executor is the daemon class that drives one. Domain conversations only need **Runtime**.
- **"attempt"** — not every execution of a batch is one. An **Attempt** is a *counted* execution, and the count is what the cap of three applies to; a **Hand-back** carrying more journalled rows than any before it, and a **Graceful stop**, leave it untouched. Say "ran again" for the execution and **Attempt** only for the counted one.
- **"context"** — three different numbers answer to it: a catalogue entry's maximum, the **Context** a batch is fitted and run at, and the window a runtime was launched with. Only the second is the domain term; name which one every time, because a batch can fit the first and fail the third.
- **"shutdown"** — a **Graceful stop** is the one the provider asks for, and it costs the batch nothing. A crash, and a prompt cancelled when the grace period ends, are not graceful and are not free. `worker_shutdown:` in a wire message marks the first of these only.
- **"journal" vs "output file"** — the **Journal** is one worker's private record of finished rows and never leaves that worker; the **Output file** is the batch's result, uploaded once, with one row per input row. A batch that is handed back has a journal and no output file.
- **"engine"** — LLaMA-Factory is the **Training engine**; Ollama and vLLM are **Runtimes**. The wire fields `engine`, `engine_version` and `engine_image` always mean the training engine.
- **"daemon" vs "runner"** — the **Daemon** executes batches and the **Runner** trains finetune jobs. They are separate processes, both registering as a **Worker** with an **Org worker key**.
- **"artifact"** — an **Artifact** is runnable weights identified by digest; a **Finetune artifact** is the record of one uploaded adapter. An adapter becomes an **Artifact** only once it is registered as a catalogue entry.
- **"base model"** — a job's **Pinned base** is the exact catalogue entry and revision it trains on; **Lineage** groups quantizations for display. Only the **Pinned base** carries a revision.
