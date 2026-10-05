# Run your prompts on Sheshnag

**Who this is for:** someone runs a Sheshnag deployment and gave you its URL.
You want to send it a batch of prompts and get results back.

*Verified against code: 2026-08-26.*

Sheshnag speaks OpenAI's Batch API, so if you have working OpenAI batch code the
migration is a `base_url` change and a different key. This page covers what the
dashboard cannot teach you: pointing code at the deployment, and driving it from
a script.

**The dashboard teaches the file format itself** — it shows a sample line,
validates what you upload before you commit to it, and explains each error. If
you are submitting your first batch, do it there once and read this afterwards.

---

## What you need

| | |
|---|---|
| **The deployment's URL** | e.g. `https://sheshnag.example.edu`. From whoever runs it. |
| **A personal API key** | Dashboard → **API Keys** → create one. The raw `gk-…` value is shown **once**; copy it then. |

Your key authenticates *you*, not a machine. It is not the same thing as the
worker keys providers use — those live under the Provider portal and are only
accepted on worker endpoints.

## Point your code at it

The base URL is your deployment's host plus `/v1`:

```python
from openai import OpenAI

client = OpenAI(
    api_key="gk-...",                                  # your personal key
    base_url="https://sheshnag.example.edu/v1",        # not api.openai.com
)
```

Or with curl:

```bash
curl -H "Authorization: Bearer gk-..." \
     https://sheshnag.example.edu/v1/models
```

`GET /v1/models` is the cheapest way to prove your key and URL work. It also
tells you **which models you may actually request** — see below.

!!! warning "`body.model` must be a catalogue id, not a raw model name"
    Every line's `body.model` has to be one of the ids from `GET /v1/models`,
    like `mistral-7b-instruct-q4-ollama` — not `mistral:7b` and not
    `gpt-4o-mini`. There is deliberately no path to run an uncatalogued model,
    and the id must be the **same on every line** of the file. A model that is
    not in the catalogue fails validation with `unsupported_model`.

## Submit, poll, download

Three calls. The whole loop:

```python
# 1. Upload the JSONL
f = client.files.create(file=open("batch.jsonl", "rb"), purpose="batch")

# 2. Create the batch
batch = client.batches.create(
    input_file_id=f.id,
    endpoint="/v1/chat/completions",
    completion_window="24h",
)

# 3. Poll until it settles
import time
TERMINAL = {"completed", "failed"}
while batch.status not in TERMINAL:
    time.sleep(10)
    batch = client.batches.retrieve(batch.id)

if batch.status == "failed":
    raise SystemExit(f"batch failed: {batch.id}")

# 4. Download results
results = client.files.content(batch.output_file_id)
open("results.jsonl", "wb").write(results.read())
```

Poll every 10–30 seconds. Batches are asynchronous by design and can sit waiting
for a worker with the right model and enough VRAM; there is no benefit to
polling faster.

### The status difference that will bite you

Sheshnag has **five** batch statuses, and they are not quite OpenAI's:

| Status | Meaning |
|---|---|
| `validating` | The file is being checked. Returned immediately on create. |
| `validated` | The file is good and the batch is waiting for a worker. |
| `in_progress` | A worker is running it. |
| `completed` | Done — `output_file_id` is populated. |
| `failed` | Terminal. Either validation rejected the file, or execution failed three times. |

Two differences from OpenAI worth knowing:

- **`validated` is extra.** OpenAI has no such status. Code that switches on
  status and assumes anything not `in_progress` or `completed` is an error will
  misbehave here. Treat `validating` and `validated` as "keep waiting".
- **`finalizing`, `expired`, `cancelling` and `cancelled` never occur.** There
  is no cancel endpoint. If your code branches on them, those branches are dead.

## When validation fails

Validation is asynchronous — `batches.create` returns straight away with
`validating`, and the batch reaches `validated` or `failed` on its own. A failed
batch carries per-line errors, each with a code (`invalid_json`,
`missing_field`, `duplicate_custom_id`, `unknown_fields`, `unsupported_model`,
…), the line number, and the offending field.

Two things to know before you go hunting:

- **At most 100 errors are stored**, but the reported total is the real count. A
  file with 5,000 broken lines lists 100 of them. Fix the pattern, not the list.
- **The dashboard is better at this than the API.** It groups errors by kind,
  shows the first few line numbers for each, and suggests a fix. If a batch
  fails validation, open it there rather than parsing error JSON.

You can also watch validation live rather than polling:
`GET /v1/batches/{batch_id}/events` is a Server-Sent Events stream.

## Parameters, and what survives

`body` on each line is an ordinary OpenAI chat-completions request, but not
every parameter reaches the model — it depends on which runtime serves it.
`stream` is always rejected, since a batch cannot stream.

- [OpenAI compatibility](reference/openai-compatibility.md) — parameter by
  parameter, honoured, translated, or dropped
- [Structured outputs](reference/structured-outputs.md) — getting
  schema-constrained JSON back

## See also

- [API reference](reference/api.md) — every endpoint, and the batch lifecycle in full
- [Model catalogue](reference/model-catalogue.md) — how a model becomes available to request
