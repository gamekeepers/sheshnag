# Provider controls

**Status:** design, closes [#104](https://github.com/gamekeepers/sheshnag/issues/104).
Part of the sprint in [#161](https://github.com/gamekeepers/sheshnag/issues/161).
Nothing here is built yet; each V1 control has its own follow-up issue.

*Verified against code on `develop` at `04d9d97`, 2026-10-03.*

**Who this is for:** you are about to implement one of the controls below, or you
need to know why a worker is or is not being given work.

---

## 1. The problem

A provider lends a machine and, from then on, has no say in what runs on it. The
scheduler honours exactly one provider decision — the worker-level drain flag:

```python
# backend/routers/workers.py:376
if worker.status == "draining":
    return {"job": None}
```

Everything else on the dispatch path is capability matching: can this worker
*technically* run this model. Drain finishes the current batch first, which can
take hours, so a provider who needs the machine *now* has one option left: stop
the daemon. [`docs/provider.md`](../provider.md#everyday-operations) tells them
that is safe — *"Any batch in flight is requeued to another worker; nothing is
lost."*

It is not safe, and what happens depends on timing:

| What happens on `systemctl --user stop gpu-daemon` mid-batch | Result |
|---|---|
| The daemon catches `SIGTERM`, stops taking new prompts, lets the in-flight ones finish (`daemon/daemon/worker.py:991`) … | |
| … and they finish inside systemd's default 90 s stop timeout | `_execute_job` carries on and **uploads the partial output as a completed batch** (`worker.py:437-449`). `upload_results` accepts it (`backend/routers/workers.py:491`). The user receives a batch marked `completed` whose output is missing every prompt that never ran; those are counted as `failed`. |
| … or an in-flight prompt outlasts 90 s (the per-prompt timeout is 300 s) | systemd sends `SIGKILL`. Nothing is uploaded. After 120 s of heartbeat silence the sweeper requeues the batch (`backend/sweeper.py:62`), `attempts` goes up by one against a cap of three (`sweeper.py:20`), and the next worker starts from prompt 1. |

Both outcomes lose work. The first one also hides it. Because providers have no
gentler control, they are pushed onto this path.

## 2. Vocabulary

**Eligible.** `can_serve(entry, worker, needs)` at `backend/scheduler.py:341` is
the single definition of "this worker may be given a batch for this model". It
checks, in order, that the worker hosts the model on a `ready` runtime, that the
runtime advertises the capabilities the rows need, and that the model fits the
runtime's fit rule. The scheduler (`find_best_batch`) and the pool-capacity
endpoint (`backend/routers/pool.py:130`) both call it, so the capacity shown to
users can never claim a model dispatch would refuse.

**Hard constraint.** Narrows the feasible set: an ineligible worker is never
offered the batch. It belongs in `can_serve`, so it moves advertised capacity
with it.

**Soft preference.** A ranking term only: changes *which* eligible batch a worker
gets first, never *whether* it can get it. It never changes advertised capacity.
Ranking is out of scope for this document (requeue affinity is
[#166](https://github.com/gamekeepers/sheshnag/issues/166)).

**Starvation.** Dispatch is worker-pull. The scheduler only ever asks "which batch
for this worker?", never "which worker for this batch?". A batch that every
capable worker refuses therefore sits in `validated` forever and nothing notices.
Every hard constraint can cause this, so every hard constraint below states what
the user is told when it does.

## 3. The controls, and the V1 cut

Ranked by provider demand against scheduler cost.

| # | Control | Provider says | Kind | Demand | Cost | Decision |
|---|---|---|---|---|---|---|
| 1 | Drain | "Finish what you are doing, take nothing new." | worker state | — | built | **Exists.** Keep as is. |
| 2 | Release | "Give my machine back now, without losing the work." | action | high | low | **V1** |
| 3 | Availability window | "Only between 18:00 and 08:00." | hard | high | low | **V1** |
| 4 | Max batch size | "Nothing over 500 prompts." | hard | medium | low | **V1** |
| 5 | Own organisation only | "Only my lab's jobs." | hard | medium | high | V2 — see below |
| 6 | Model opt-out | "Never run model X here." | hard | low | low | **Not built.** Already possible — see below |
| 7 | RAM reserve | "Leave 8 GB of system RAM for me." | hard | low | medium | Deferred to [#164](https://github.com/gamekeepers/sheshnag/issues/164) |
| 8 | Daemon-side limits | "Advertise less VRAM", "fewer prompts at once." | daemon config | — | built | **Exists** as `DAEMON_VRAM_GB` and `DAEMON_MAX_CONCURRENT_PROMPTS`. Document only. |

**Why not own-organisation-only in V1.** A batch records `user_id` but no
organisation (`backend/models.py:594`), and a user can belong to several, so the
check needs a definition of "the batch's organisation" first. And the pool
snapshot is computed once and cached for every viewer (`pool.py:39`); a tenant
restriction makes capacity depend on who is asking, which that cache cannot
express. Both are solvable, neither is small.

**Why not model opt-out.** A worker that does not host a model is already never
offered it — that is the first check in `can_serve`. A provider who does not want
a model on their machine removes it (`ollama rm <model>`) and gets the same result
with no new code.

## 4. V1 controls in detail

### 4.1 Release

**What the provider does:** stops the daemon, exactly as today. Nothing new to
learn, and the sentence in `provider.md` becomes true.

**Daemon.** On `SIGTERM`/`SIGINT` with a batch in flight:

1. Stop taking new prompts (already the behaviour).
2. Give in-flight prompts a short grace period, well inside systemd's 90 s stop
   timeout — 30 s is the suggested value. Cancel whatever is still running after it.
3. If every prompt finished, upload as normal.
4. Otherwise **do not upload**. Call `POST /workers/release` and exit.

Step 4 is the fix for the partial-upload bug in section 1, and is worth landing
on its own even before the endpoint exists (see section 9).

**Server — `POST /workers/release`** (worker-key auth, same as `report-failure`):

- Checks the assignment belongs to the caller, as `_get_assigned_batch` does today.
- Requeues the batch: assignment deleted, status back to `validated`, progress
  counters reset — the same steps as `requeue_or_fail_batch`, **without**
  incrementing `attempts`.
- Increments `batches.releases`.
- Marks the worker `offline` immediately, instead of waiting 120 s for the sweeper,
  so capacity stops counting a machine that is going away. Its next heartbeat
  brings it back.

**Attempts exemption rule.** A refusal the provider *chose* does not count against
the batch; a refusal caused by the batch or the machine does. In V1 release is
the only exempt path. Every existing `report-failure` cause — model missing, pull
failed, empty input, executor error — keeps counting.

**Loop guard.** Releases have their own counter so a batch cannot bounce between
machines forever. Past `MAX_BATCH_RELEASES` (suggested: 5), a further release is
treated as an ordinary failure and counts as an attempt.

**Hard or soft:** neither — an action, not an eligibility rule.
**Starvation:** none. The batch goes straight back to the queue.

The completed prompts survive only once worker-local journalling lands (the
resumability sprint); until then release saves the attempt and the 120 s wait,
not the prompts. Getting the batch back to the worker that holds the journal is
[#166](https://github.com/gamekeepers/sheshnag/issues/166).

### 4.2 Availability window

**What the provider sets:** a start time, an end time and an IANA timezone, for
example `18:00`–`08:00` `Asia/Kolkata`. Unset means always available. A start
later than the end wraps past midnight. Start equal to end is rejected.

**Rule:** outside the window the worker is not eligible to **start** a batch. A
batch already running when the window closes finishes. This is deliberately not
"only start a batch that will finish before the window closes": that needs a
duration estimate the platform cannot produce today.

**Hard.** Checked inside `can_serve`, so it applies to dispatch and to capacity
from one place: a worker outside its window simply stops counting.

**Starvation:** temporary only — the window always reopens, so the batch is not
failed. The user is owed a visible reason while it waits, for example *"waiting:
the workers that can run this are outside their available hours"* (surfaced by
[#165](https://github.com/gamekeepers/sheshnag/issues/165)).

### 4.3 Max batch size

**What the provider sets:** a prompt count. Unset means no limit.

**Rule:** a batch with more prompts than the limit (`batches.request_counts_total`)
is never offered to this worker.

**Hard.** `can_serve` grows an optional `prompts` argument. Dispatch passes the
batch's count; the capacity endpoint passes nothing and instead reports, per
servable model, the largest batch some eligible online worker accepts (`null` if
any of them has no limit). A user can see the ceiling before submitting.

**Starvation:** permanent, so it is caught at validation. When
`validate_batch_file` (`backend/services/batch_validator.py:496`) has counted the
prompts, it asks whether **any registered worker** would accept the batch,
ignoring availability windows and liveness, which are temporary:

| Situation | Outcome |
|---|---|
| No registered worker accepts a batch this large for this model | Validation **fails**, the same way a malformed file does, with a message that names the limit: *"No worker accepts more than 500 prompts for this model — split the file."* |
| Only workers that are offline right now would | Batch is `validated` and waits, with a visible reason ([#165](https://github.com/gamekeepers/sheshnag/issues/165)). |
| An online worker would | Normal. |

## 5. Where each control is enforced

| Control | Enforced | Why |
|---|---|---|
| Release | daemon **and** server | Only the daemon knows it is being stopped; only the server can requeue. |
| Availability window | server | Decidable from the worker row and the clock. Server-side keeps capacity honest. |
| Max batch size | server | Decidable from the batch row. Same reason. |

**Trust.** Every V1 control only narrows what the provider who set it receives.
A provider who misstates one only reduces their own share, so a provider-declared
value is safe to use as a server-side filter without a daemon-side backstop. A
backstop is needed only for state the server cannot see — live local use of the
GPU — and no V1 control depends on that.

**Where controls live.** Per worker, set by an organisation owner or admin — the
same roles that can drain today
(`backend/routers/organizations.py:521`). Organisation-wide defaults with
per-worker overrides double the surface and nothing in V1 needs them.

## 6. Advertised capacity

The rule: **every hard constraint lives in `can_serve`, and nowhere else.** The
pool endpoint already builds `models_servable` from `can_serve`, so a constraint
placed there narrows capacity automatically. A constraint placed anywhere else —
in `poll_job`, say, next to the drain check — makes the advertised figure wrong.

What the pool endpoint must change:

- Workers outside their availability window stop counting (automatic, via `can_serve`).
- Each servable model gains the largest batch size an eligible online worker
  accepts (section 4.3).
- A model with no eligible worker at all is reported as such rather than silently
  omitted — the single most useful thing the strip can say. That, and queue depth,
  are [#165](https://github.com/gamekeepers/sheshnag/issues/165).

**Audit.** A silent filter makes "why is my GPU getting no work?" unanswerable.
The eligibility check is therefore written to return *why* a worker is not
eligible, with `can_serve` defined as "no reason". Reason codes:
`model_not_hosted`, `runtime_not_ready`, `missing_capability`, `does_not_fit`,
`outside_window`, `batch_too_large`, `draining`. Surfacing them in the provider
portal is frontend work and out of scope here.

## 7. Ruling on `WorkerRuntime.status`

**Enforce it — and it already is.** #104 was written when nothing read the
column. Since then `WorkerRuntime.schedulable` (`backend/models.py:325`) admits
only `ready`, and `_hosting_runtime` (`backend/scheduler.py:300`) and
`Worker.advertised_models()` skip any runtime that is not schedulable, so a
`draining` or `unavailable` runtime receives no work and is not advertised. No
change needed.

## 8. Schema sketch

`create_all()` never adds a column to an existing table, so each of these needs an
`ADD COLUMN` entry in `backend/migrations.py` ([quirk](../develop.md#backend)). All are
nullable; NULL means "no constraint", so existing rows keep today's behaviour.

**`workers`**

| Column | Type | Meaning |
|---|---|---|
| `window_start_min` | `INTEGER` | Minutes after local midnight, 0–1439. NULL = always available. |
| `window_end_min` | `INTEGER` | Same. Less than start ⇒ the window wraps midnight. |
| `window_tz` | `VARCHAR` | IANA name, read with `zoneinfo`. |
| `max_batch_prompts` | `INTEGER` | NULL = no limit. |

**`batches`**

| Column | Type | Meaning |
|---|---|---|
| `releases` | `INTEGER` | Provider releases, counted apart from `attempts`. Read as `or 0`. |

**Endpoints**

- `POST /workers/release` — daemon, worker key.
- `PATCH /orgs/{org_id}/workers/{worker_id}/policy` — owner/admin; sets the four
  `workers` columns.

## 9. Follow-up work

| Work | Issue |
|---|---|
| Daemon stops uploading partial output as `completed` on shutdown — report a failure instead until release exists. A live bug, independent of the rest. | new |
| Release: daemon grace period, `POST /workers/release`, `batches.releases`, attempts exemption, `provider.md` wording | new |
| Availability window and max batch size: columns, policy endpoint, checks in `can_serve`, validation-time starvation check, reason codes | [#163](https://github.com/gamekeepers/sheshnag/issues/163) |
| Capacity: batch-size ceiling, "no eligible worker", queue depth | [#165](https://github.com/gamekeepers/sheshnag/issues/165) |
| Context-aware VRAM fit | [#164](https://github.com/gamekeepers/sheshnag/issues/164) |
| Requeue affinity | [#166](https://github.com/gamekeepers/sheshnag/issues/166) |
| Own organisation only | V2, not filed |
