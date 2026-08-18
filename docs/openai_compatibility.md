# OpenAI Parameter Compatibility Matrix

The Sheshnag platform claims "existing OpenAI batch code migrates by
changing `base_url`." This document records which OpenAI sampling
parameters are actually honoured, translated, or dropped by each
supported inference runtime, so the claim is either true or narrowed to
state exactly what is supported.

> [!WARNING]
> **Doc-derived, live verification pending.** This matrix was built from
> the official Ollama `/api/chat` documentation and vLLM OpenAI-compatible
> server documentation — NOT from live-server testing. Both projects move
> faster than their docs; actual behaviour may differ. Entries marked
> "needs live testing" are best-effort from docs and must be confirmed
> against a running server before being treated as ground truth.

---

## Compatibility Table

| Parameter | OpenAI location | Ollama (`/api/chat`) | vLLM (`/v1/chat/completions`) |
|---|---|---|---|
| `temperature` | top-level | **translated** → `options.temperature` | **native** — accepted top-level †|
| `max_tokens` | top-level | **translated** → `options.num_predict` | **native** — accepted top-level †|
| `top_p` | top-level | **translated** → `options.top_p` | **native** — accepted top-level †|
| `top_k` | non-standard | **translated** → `options.top_k` | **ignored** — warn-and-drop; vLLM does not accept `top_k` as a top-level field; nest it under `"extra_body"` in the raw JSON body instead (this applies to the wire protocol, not just the Python client's `extra_body=` kwarg) — UNVERIFIED, needs live testing |
| `stop` | top-level | **translated** → `options.stop` | **native** — accepted top-level †|
| `seed` | top-level | **translated** → `options.seed` | **native** — accepted top-level †|
| `frequency_penalty` | top-level | **translated** → `options.frequency_penalty` | **native** — accepted top-level †|
| `presence_penalty` | top-level | **translated** → `options.presence_penalty` | **native** — accepted top-level †|
| `n` | top-level | **rejected at submission** — Ollama produces exactly 1 completion per request; `n=1` is accepted, `n>1` is rejected at batch submission time (before processing begins) | **native** — accepted top-level †|
| `logprobs` | top-level | **ignored** — warn-and-drop; Ollama does not expose log-probabilities | **native** — accepted top-level †|
| `top_logprobs` | top-level | **ignored** — warn-and-drop; Ollama does not expose log-probabilities | **native** — accepted top-level †|
| `tools` | top-level | **translated** — passed top-level (Ollama native since ~0.3) | **native** — accepted top-level †|
| `tool_choice` | top-level | **ignored** — warn-and-drop; Ollama does not support `tool_choice` — tool selection is determined by the model from the `tools` list | **native** — accepted top-level †|
| `stream` | top-level | **rejected** — batch execution cannot honour streaming; response shape is incompatible | **rejected** — batch execution cannot honour streaming; response shape is incompatible |
| `response_format` | top-level | **translated** → `format` (implemented in issue #41, structured outputs) | **native** — accepted top-level †|

> **† UNVERIFIED** — The vLLM column was built from vLLM's OpenAI-compatible server documentation, not from a live server. No vLLM instance was available in this environment (no GPU, no vllm package). Entries marked native † are doc-derived and must be confirmed against a running server before being treated as ground truth.

---

## Legend

| Status | Meaning |
|---|---|
| **native** | The runtime accepts this parameter in the same position as the OpenAI API. No translation needed. |
| **translated** | The daemon maps the OpenAI parameter to the runtime's equivalent (e.g. `max_tokens` → `options.num_predict`). |
| **ignored** | The parameter cannot be honoured. The daemon logs a warning and drops it. The request still executes — the parameter is simply absent. |
| **rejected** | The parameter is fundamentally incompatible. The daemon returns a `CompletionResult` with a descriptive error. The request does not execute. |

---

## Warn-and-Drop Policy

When a parameter is marked **ignored**, the daemon:

1. Logs a `WARNING`-level message identifying the parameter, the prompt's
   `custom_id`, and the reason it cannot be honoured.
2. Omits the parameter from the translated request.
3. Proceeds with execution — the prompt is **not** failed.

There are **no silent drops**. Every parameter that cannot be forwarded
produces a log entry.

---

## Reject-at-Submission (Backend)

### `n > 1` on Ollama — **Implemented** (issue #49)

Ollama batches with `n > 1` in any row are rejected at submission time
(POST `/v1/batches`), before the job reaches any worker. The backend
checks the model's `runtime` field in the catalogue:

- **`runtime=ollama`** + any row with `n > 1` → entire batch rejected with
  `unsupported_parameter` error naming the exact lines and values.
- **`runtime=vllm`** → `n > 1` accepted normally, passes through to vLLM natively.

Integration point: [`batch_validator.py`](../backend/services/batch_validator.py) —
`_CrossFileContext.n_gt1_rows` accumulates offending rows; phase 4b of
`validate_batch_file()` issues the catalogue lookup and applies the gate.

### Other unsupported parameters — **Not yet implemented**

A future enhancement could reject additional unsupported parameters at batch
submission time (before the job reaches any worker). The integration point for
further work is:

- [`batch_validator.py`](../backend/services/batch_validator.py):
  `_validate_chat_body()` (line ~192) and `ENDPOINT_VALIDATORS` (line ~226)
- Requires looking up the model's runtime type from the catalogue during
  validation to apply runtime-specific parameter allowlists.
- **Flagged for backend-owner coordination** — out of scope beyond the n>1 gate above.

---

## Batch-Specific Rejections

### `stream: true`

Batch execution is inherently non-streaming. If `stream: true` appears in
a request body, the daemon rejects the prompt **before** it reaches any
executor, with error code `UNSUPPORTED_PARAMETER`. This applies to all
runtimes uniformly.

The rejection lives in `Worker._run_prompts()` in `daemon/daemon/worker.py`,
not in any executor. This is intentional — executors handle runtime-specific
translation, not batch-mode policy. Stream rejection is a batch-mode concern.

### `n > 1` — Runtime-dependent

**Backend (submission-time gate):** For Ollama-runtime models, `n > 1` is
rejected at submission time (see Reject-at-Submission above). The batch
never reaches the worker.

**Ollama executor:** `OllamaExecutor._translate_request()` also enforces
`n > 1` as a hard reject in case a row somehow bypasses the submission gate
(defence-in-depth). Ollama's `/api/chat` produces exactly one completion
per request.

**vLLM executor:** `VLLMExecutor` has no `n` restriction — vLLM supports
`n` natively and returns all completions in `choices[]`.

---

## vLLM-Native Extensions

The following parameters are vLLM-specific and have no Ollama equivalent.
Since `VLLMExecutor` posts the request body as-is (minus params in
`_UNSUPPORTED_TOP_LEVEL`), these keys pass through to the vLLM server
unchanged. They are **not** translated, validated, or type-checked by the
daemon.

| vLLM-native parameter | Status |
|---|---|
| `min_p` | Pass-through — vLLM-specific, no Ollama equivalent |
| `repetition_penalty` | Pass-through — vLLM-specific, no Ollama equivalent |
| `best_of` | Pass-through — vLLM-specific, no Ollama equivalent |
| `use_beam_search` | Pass-through — vLLM-specific, no Ollama equivalent |
| `logit_bias` | Pass-through — vLLM-specific, no Ollama equivalent |
| `echo` | Pass-through — vLLM-specific, no Ollama equivalent |
| `guided_json` / `guided_regex` / `guided_choice` / `guided_grammar` | Pass-through — vLLM constrained decoding, no Ollama equivalent |

> [!NOTE]
> "Pass-through" means the daemon forwards the key verbatim. Whether vLLM
> actually honours it depends on the installed vLLM version and how the
> server was started. These are explicitly OUT OF SCOPE for the daemon's
> translation layer — the caller is responsible for ensuring the vLLM server
> supports the parameter.
