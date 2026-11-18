# Finetuning job contract

What a finetuning job **is** on Sheshnag, written down once so the API and the
runner are built against the same thing. Code: `backend/finetune_contract.py`
(constants and limits), `backend/models.py` (tables),
`backend/routers/finetune.py` (user API), `backend/routers/workers.py`
(`/workers/finetune-*`, the runner side), and
`backend/services/finetune_validator.py`.

- **Serving is vLLM 0.31.0.** An artifact you cannot route to is not a
  deliverable. So the job carries what serving needs, and the platform
  enforces what vLLM itself does not check.
- **The engine is LLaMA-Factory 0.9.5**, pinned by image digest in
  `finetune_contract.ENGINE_IMAGE`.

## Shape

It is Sheshnag's own endpoint. It uses the same credentials as `/v1/files` and
`/v1/batches` (a JWT or a personal `gk-` key), the same `/v1/files` upload, and
the same backend. The request and response shapes follow OpenAI's fine-tuning
API on purpose, plus one Sheshnag-specific block: `lora`.

| Route | |
|---|---|
| `POST /v1/fine_tuning/jobs` | create; returns the job in `validating_files` |
| `GET /v1/fine_tuning/jobs?after=&limit=` | list, newest first |
| `GET /v1/fine_tuning/jobs/{id}` | one job |
| `POST /v1/fine_tuning/jobs/{id}/cancel` | cancel; a running job stops at its next progress report |
| `GET /v1/fine_tuning/jobs/{id}/events?after=&limit=` | event log, newest first |
| `GET /v1/fine_tuning/jobs/{id}/artifacts` | adapters (and later checkpoints) with servability verdicts |

```jsonc
// POST /v1/fine_tuning/jobs: everything except model and training_file is optional
{
  "model": "qwen2-5-0-5b-instruct-hf",        // catalogue id, source_type=huggingface
  "training_file": "file-…",                  // uploaded with purpose="fine-tune"
  "validation_file": null,
  "organization_id": null,                    // org the job runs under; default: the caller's only org
  "suffix": "support-bot",                    // [a-z0-9-], ≤40; folded into fine_tuned_model
  "seed": 42,
  "method": {"type": "supervised"},
  "hyperparameters": {"n_epochs": 3, "batch_size": 16, "learning_rate": 2e-4, "max_seq_length": 1024},
  "lora": {"rank": 16, "alpha": 32, "dropout": 0.05, "target_modules": "all-linear"}
}
```

**Unknown keys are rejected (422), at every level.** A typo'd `lora_rnk` that
was silently dropped would train with the wrong rank, or crash at model load
with an error that never names the key.

`batch_size` is the *effective* batch. The runner chooses micro-batch ×
gradient accumulation to fit the worker's free VRAM. On a shared GPU that is a
runtime fact, not a user choice.

## Lifecycle

```
validating_files ──► queued ──► running ──► succeeded
        │              │           │  └────► failed
        └──► failed    │           └──► queued      (requeue, up to 3 attempts)
   any non-terminal ───┴───────────────► cancelled
```

Every transition goes through `services/finetune_events.transition()`. It is
checked against `VALID_TRANSITIONS`, stamps `queued_at`, `started_at` or
`finished_at`, and writes an event.

## Limits serving imposes

| Constraint | Why | Enforced |
|---|---|---|
| `lora.rank ∈ {8, 16, 32, 64}` | Serving runs vLLM with `--max-lora-rank 64`. An adapter with a larger `r` doesn't just fail to load: **the whole server fails to start.** | 422 at create; artifact re-checked at upload |
| `target_modules` ⊆ `q,k,v,o,gate,up,down_proj`, or `"all-linear"` | `all-linear` on Qwen2 dense resolves to exactly these 7 and serves. `lm_head`, `embed_tokens` and `modules_to_save` were not tested. | 422 at create; artifact re-checked at upload |
| Base model pinned on the job (`base_source_ref` + `base_source_revision`) | vLLM never checks an adapter's base. A shape-identical wrong base loads silently and returns garbage with HTTP 200. | Snapshotted at create; the adapter's `base_model_name_or_path` must match at upload |

The base model's `base_model_name_or_path` is free text with no revision, so a
match is necessary but not sufficient. **The job's pinned revision is the
identity serving must load.** The catalogue stores it as a commit SHA,
and the API refuses to finetune a base whose revision is not a SHA.

**A base is trainable only if its catalogue entry has a `chat_template`**
(LLaMA-Factory's name for the template the base was trained with, e.g.
`qwen`). A wrong template trains without error and serves garbage, so it is
curated, never guessed. The API refuses a job on a base without one (400,
naming the bases that are trainable), and the runner trains with the value
from the poll payload, so there is one list.

## Tables

All three are new tables; `batches` gets no new columns. `create_all()` gives
everyone a new table for free, but it can never add a column.

### `finetune_jobs`

Who and what: `user_id`, `api_key_id`, `org_id`. `org_id` is the org the
caller named in `organization_id` (they must be a member, or the request is a
403) or, when they named none, their only org; it is NULL when they belong to
several and named none. Workers claim queued jobs from every org, so a job's
dataset can reach any provider's runner; filtering the claim on `org_id`
keeps it on the job's own org. The pinned base: `base_model_id`,
`base_source_ref`, `base_source_revision`. Inputs: `training_file_id`,
`validation_file_id`. Then `method`, `suffix`, `seed`. Hyperparameters:
`n_epochs`, `batch_size`, `learning_rate`, `max_seq_length`. LoRA:
`lora_rank`, `lora_alpha`, `lora_dropout`, `target_modules`. State: `status`,
`error` (JSON `{code, param, message, details?}`), `attempts`. Results:
`training_file_stats` (validator output), `trained_tokens`, `fine_tuned_model`
(the serving name, `ft:<base>:<suffix>:<id>`). Timestamps.

### `finetune_events`

An append-only log of `level` (info, warn, error), `type` (message, metrics),
`message` and `data` (JSON). Progress lives here, not in columns: a loss curve
is a series. The runner's source is LLaMA-Factory's `trainer_log.jsonl`, one
JSON object per log step, and each line becomes one `metrics` event. The
integer id is the pagination cursor (`ftevent-<n>`).

### `finetune_artifacts`: the fields serving needs

| Field | Source | Why serving needs it |
|---|---|---|
| `file_id`, `sha256`, `bytes` | backend | Locate the adapter and verify it is the one validated |
| `base_model_id`, `base_source_ref`, `base_source_revision` | job | Load exactly this base; vLLM won't check |
| `peft_type` | `adapter_config.json` | Must be `LORA` |
| `lora_rank` (`r`) | `adapter_config.json` | Must be ≤ the server's `--max-lora-rank` |
| `lora_alpha` | `adapter_config.json` | Scaling; recorded for reproduction |
| `target_modules` (resolved list) | `adapter_config.json` | Must be the proven-servable set |
| `modules_to_save` | `adapter_config.json` | Must be empty to serve as a plain LoRA |
| `engine`, `engine_version`, `engine_image` | worker (self-reported) | Reproduction; the pinned engine stack |
| `metrics` | worker | Final train/eval loss |
| `servable`, `unservable_reasons` | backend | The verdict, from `adapter_unservable_reasons()` |
| `kind`, `step` | worker | `adapter` now; `checkpoint` rows are where resume will come from |
| `catalog_entry_id` | later | Set when the adapter is registered as a catalogue entry under its base. Registration is a write to this column, not a schema change. |

The backend reads everything adapter-related **from the uploaded zip's
`adapter_config.json`**, not from the worker's report. If the adapter is not
servable, the artifact row is still kept for inspection, but the job ends
`failed` with `error.code = "unservable_adapter"`.

The runner must also chmod adapter files readable before zipping. Engines
write `adapter_model.safetensors` as `0600`.

## Dataset validator

The format is chat JSONL, one object per line, UTF-8 (a BOM is tolerated):
`{"messages": [{"role": "system"|"user"|"assistant", "content": "…"}, …]}`.
A system turn may only come first. Turns must alternate user, assistant,
user, … (the engine silently drops an example that doesn't, so the validator
refuses it). The last turn must be `assistant`. A file needs at least 10
examples and at most 50,000, and 100 MB.

### Tool calling

Lines may use OpenAI's tool-calling chat format, the shape
`/v1/chat/completions` uses:

```json
{"tools": [{"type": "function", "function": {"name": "get_weather", "parameters": {...}}}],
 "messages": [
   {"role": "user", "content": "Weather in Paris?"},
   {"role": "assistant", "content": null,
    "tool_calls": [{"id": "call_1", "type": "function",
                    "function": {"name": "get_weather", "arguments": "{\"city\": \"Paris\"}"}}]},
   {"role": "tool", "tool_call_id": "call_1", "content": "{\"temp_c\": 18}"},
   {"role": "assistant", "content": "It's 18 °C in Paris."}]}
```

Rules, each refused with the line and a fix:

- every called function is declared in that line's `tools`;
- `arguments` is a string that parses to a JSON object (`"{}"` for none);
- every call has an `id`, and each is answered exactly once by a `tool` turn
  whose `tool_call_id` matches, right after the calling turn and before the
  next user or assistant turn;
- a turn that calls tools has `content: null`. The engine cannot train text
  and a call in the same turn; put the text in the reply after the results.
- an example may end on a tool call (teaching *when* to call).

Several results for one turn (parallel calls) count as one user-side turn.
Only bases whose chat template has a tool-call format accept these datasets
(`TOOL_CALLING_TEMPLATES`, today `qwen`); anything else fails validation with
`tools_unsupported_base`, naming the bases that work. Token counts include
the tool schemas (plus about 80 tokens of tool prompt), each call as rendered,
and the results; `n_tool_examples` counts the lines that use tools.

forge-runner converts each line to LLaMA-Factory's `sharegpt` layout
(`function_call` / `tool` turns and a `tools` column) rather than using its
`openai` formatting, which appends "detailed thinking off" to system prompts
and double-encodes `arguments`. On `qwen` the result is Qwen2.5's native
`<tool_call>{"name": …, "arguments": {…}}</tool_call>` text, the format
vLLM's `hermes` tool parser reads.

**Output**, stored in `training_file_stats` and in a validation event:

- `n_examples`
- `total_tokens`
- `min`, `p50`, `p95`, `max`
- `n_truncated`: examples over `max_seq_length`. These produce a `warn`
  event, not a failure.
- a length `histogram` with bucket edges at 128, 256, 512, … 8192, and then
  everything above 8192.

Tokens are counted with the base model's own tokenizer, fetched from the HF hub
via `tokenizers`, plus 5 template tokens per turn. If the hub is unreachable,
it falls back to a chars/4 estimate. `token_count_method` records which method
was used, so an estimate is never mistaken for an exact count.

**Every error says how to fix it.** Each error is
`{line, code, field, message, hint}`. The common wrong formats are recognised
by name, each with a conversion hint:

- prompt/completion
- Alpaca instruction/output
- ShareGPT conversations with from/value
- a JSON array instead of JSONL
- tool results as role `function` or `observation`
- a pretty-printed multi-line record
- role typos such as `human` or `gpt`

The job's `error.message` is the first problem plus its fix, for example:
`line 1: prompt/completion pairs are not supported — fix: convert each line to
{"messages": …} (and 11 more)`.

## Runner protocol (`/workers/finetune-*`, worker `gk-` key)

The runner is forge-runner ([`runner/`](https://github.com/gamekeepers/sheshnag/blob/develop/runner/README.md)), a separate
process from the batch daemon. It registers and heartbeats like any worker
(`/workers/register`, `/workers/{id}/heartbeat`), including while it
supervises a run.

1. `POST /workers/finetune-poll`: claims the oldest `queued` job whose
   training VRAM fits the worker's **free** VRAM (from its last heartbeat),
   moves it to `running`, and returns the hyperparameters, the pinned base,
   its `chat_template`, `dataset_path`, `engine` and `engine_image`. The
   runner runs the `engine_image` it is sent, so the backend's pin is the one
   used.
2. `GET <dataset_path>`: workers may read files belonging to a job assigned to
   their org. A file shared by several jobs (a hyperparameter rerun) is
   readable by every org that has one of those jobs assigned.
3. `POST /workers/finetune-progress`: send one per trainer log step
   (`current_step`, `total_steps`, `current_epoch`, `loss`, `learning_rate`,
   `eval_loss`). The response's `stop: true` means the job was cancelled, so
   kill the engine.
4. `POST /workers/finetune-upload-adapter`: multipart. Send the zip (containing
   `adapter_config.json` and `adapter_model.safetensors`) plus `engine`,
   `engine_version`, `engine_image`, and `metrics` (JSON).
5. `POST /workers/finetune-report-failure`: `error` (shown to the user),
   `code`, and `retryable`. A retryable failure requeues the job, which
   restarts from step 0, and the event log says so. It fails for good after 3
   attempts. A non-retryable failure (bad config, nothing to train on, base
   unavailable) fails at once. The runner reports failures outside the
   engine too: `engine_not_started` (e.g. no docker), `adapter_upload_failed`,
   and `runner_error` for anything unexpected, so one bad job never takes the
   runner down. A 409 on upload means the job was cancelled after training;
   the runner keeps the adapter on disk and reports nothing.

**Training is never restarted silently.** The worker sweep that requeues
batches leaves finetune jobs alone. If a running job's runner stops
heartbeating for 10 minutes, the job is **failed** with `runner_lost` and a
message telling the user to resubmit. It is never requeued from step 0
without their knowledge. The message does not name the worker: consumers
never see a provider's hostname.

## Outside this contract

- **DPO and ORPO.** The engine supports them (`stage: dpo`,
  `pref_loss: sigmoid|orpo|simpo`). Adding them means a new `method.type` and
  a preference-pair validator, not new tables.
- **Resume from checkpoint.** A retry restarts from step 0. `kind='checkpoint'`
  artifacts carry the resume point.
- **The adapter does not record its base revision.** PEFT writes
  `"revision": null` into `adapter_config.json`. The job's pinned revision is
  the only record of it.
- **Registering the adapter in the catalogue.** Until an adapter has a
  catalogue entry, it cannot be named in `POST /v1/batches`; registration is
  one write to `catalog_entry_id`.

## Open items

- The backend needs outbound HF hub access for exact token counts. Without
  it, stats are estimates. Set `HF_TOKEN` for rate limits.
- Free VRAM is as fresh as the last heartbeat (30 s). The runner's
  `--min-free-vram-gb` check just before it polls is the second guard.
- Any provider's runner may claim any org's job (#193). Tighten by filtering
  the claim on `finetune_jobs.org_id` before users outside the team get access.

## Fixed rules

- Catalogue HF entries must be pinned to commit SHAs. The API refuses to
  finetune any base whose `source_revision` is not a 40-hex SHA.
- Training VRAM is estimated as 3.2× the catalogue's inference `vram_gb`,
  measured for bf16 LoRA at the contract defaults.
