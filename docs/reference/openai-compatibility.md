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
| `temperature` | top-level | **translated** → `options.temperature` | **native** — accepted top-level |
| `max_tokens` | top-level | **translated** → `options.num_predict` | **native** — accepted top-level |
| `top_p` | top-level | **translated** → `options.top_p` | **native** — accepted top-level |
| `top_k` | non-standard | **translated** → `options.top_k` | **ignored** — warn-and-drop; vLLM requires `extra_body` wrapping, not a top-level key (doc-derived, needs live testing) |
| `stop` | top-level | **translated** → `options.stop` | **native** — accepted top-level |
| `seed` | top-level | **translated** → `options.seed` | **native** — accepted top-level |
| `frequency_penalty` | top-level | **translated** → `options.frequency_penalty` | **native** — accepted top-level |
| `presence_penalty` | top-level | **translated** → `options.presence_penalty` | **native** — accepted top-level |
| `n` | top-level | **rejected** — Ollama produces exactly 1 completion per request; `n=1` is accepted, `n > 1` returns an error | **native** — accepted top-level |
| `logprobs` | top-level | **ignored** — warn-and-drop; Ollama does not expose log-probabilities | **native** — accepted top-level |
| `top_logprobs` | top-level | **ignored** — warn-and-drop; Ollama does not expose log-probabilities | **native** — accepted top-level |
| `tools` | top-level | **translated** — passed top-level (Ollama native since ~0.3) | **native** — accepted top-level |
| `tool_choice` | top-level | **ignored** — warn-and-drop; Ollama does not support `tool_choice` — tool selection is determined by the model from the `tools` list | **native** — accepted top-level |
| `stream` | top-level | **rejected** — batch execution cannot honour streaming; response shape is incompatible | **rejected** — batch execution cannot honour streaming; response shape is incompatible |
| `response_format` | top-level | **translated** → `format` (implemented in issue #41, structured outputs) | **native** — accepted top-level |

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

## Reject-at-Submission (Backend — Not Yet Implemented)

A future enhancement could reject unsupported parameters at batch
submission time (before the job reaches any worker), providing instant
feedback to the user. The integration point is:

- [`batch_validator.py`](https://github.com/gamekeepers/sheshnag/blob/develop/backend/services/batch_validator.py):
  `_validate_chat_body()` (line 192) and `ENDPOINT_VALIDATORS` (line 226)
- Requires looking up the model's runtime type from the catalogue during
  validation to apply runtime-specific parameter allowlists.
- **Flagged for backend-owner coordination** — not implemented in this PR.

---

## Batch-Specific Rejections

### `stream: true`

Batch execution is inherently non-streaming. If `stream: true` appears in
a request body, the daemon rejects the prompt **before** it reaches any
executor, with error code `UNSUPPORTED_PARAMETER`. This applies to all
runtimes uniformly.

### `n > 1` (Ollama only)

Ollama's `/api/chat` produces exactly one completion per request. If
`n > 1` is specified, the Ollama executor raises an error and the prompt
is marked as failed. vLLM supports `n` natively.
