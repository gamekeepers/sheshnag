# Finetune a model on Sheshnag

**Who this is for:** someone runs a Sheshnag deployment and gave you its URL.
You have examples of the answers you want, and you want a LoRA adapter that
teaches a base model to give them.

*Verified against a running backend and forge-runner on an RTX 6000 Ada:
2026-11-18. Every request and response below is copied from that run, with
the URL and ids replaced.*

You upload a chat dataset, submit a job with your API key, follow its
progress, and get back a LoRA adapter that has already been checked to load
on vLLM. The request shapes follow OpenAI's fine-tuning API, with the
differences listed under [Limits](#limits). There is no dashboard view for
finetuning yet; this page is the whole user surface.

The contract behind it (statuses, tables, the worker protocol) is in the
[finetuning contract](reference/finetune-contract.md). You don't need it to
use the API.

---

## What you need

| | |
|---|---|
| **The deployment's URL** | e.g. `https://sheshnag.example.edu`. From whoever runs it. |
| **A personal API key** | Dashboard → **API Keys** → create one. The raw `gk-…` value is shown **once**. |
| **A trainable base model** | Today `qwen2-5-0-5b-instruct-hf` (Qwen2.5-0.5B-Instruct) and `tinyllama-1-1b-chat-hf` (TinyLlama-1.1B-Chat). A job on any other model is refused with a 400 that names the trainable ones. |
| **A chat-JSONL file** | At least 10 examples. See below. |

The examples use these two variables:

```bash
export SHESHNAG_URL=https://sheshnag.example.edu
export SHESHNAG_API_KEY=gk-...
```

## Prepare your data

One JSON object per line, UTF-8, the same `messages` shape chat completions
use:

```json
{"messages": [{"role": "system", "content": "You answer in one sentence."}, {"role": "user", "content": "What is LoRA?"}, {"role": "assistant", "content": "A way to finetune a small set of extra weights instead of the whole model."}]}
```

- A `system` turn is optional and may only come first.
- Turns alternate user, assistant, user, … and the last turn is `assistant`:
  that is the reply the model learns to produce.
- 10 to 50,000 examples, at most 100 MB. A few hundred is typical for a LoRA
  to pick up a style or a format.
- Examples longer than `max_seq_length` tokens (default 1024) are **truncated,
  not rejected**: the job gets a `warn` event saying how many. Raise
  `max_seq_length` or shorten them if the end of the answer matters.

Tokens are counted with the base model's own tokenizer at its pinned
revision, so the counts in the job's `training_file_stats` are exact; the
`token_count_method` field says so.

### Tool calling

To teach a model to call your functions, use OpenAI's tool-calling chat
format: the tool schemas in `tools`, an assistant turn with `tool_calls` and
`content: null`, a `tool` turn per call with the result, and the final answer.

```json
{"tools": [{"type": "function", "function": {"name": "get_weather", "description": "Current weather for a city", "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}}], "messages": [{"role": "user", "content": "What's the weather in Paris?"}, {"role": "assistant", "content": null, "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "get_weather", "arguments": "{\"city\": \"Paris\"}"}}]}, {"role": "tool", "tool_call_id": "call_1", "content": "{\"temp_c\": 18, \"sky\": \"clear\"}"}, {"role": "assistant", "content": "It's 18 °C and clear in Paris."}]}
```

- `arguments` is a **string** of JSON, as OpenAI sends it.
- Every called function must be declared in that line's `tools`.
- Each call is answered once, by a `tool` turn with the matching
  `tool_call_id`, before the next user or assistant turn. Parallel calls are
  fine: one assistant turn with several calls, then one `tool` turn each.
- A turn that calls tools has no text. Put the text in the reply after the
  results.
- An example may end on the tool call itself, to teach *when* to call.
- Tool calling trains only on **`qwen2-5-0-5b-instruct-hf`**. TinyLlama's chat
  template has no tool-call format, so a tool dataset on it fails validation
  with `tools_unsupported_base`.

The model learns Qwen2.5's native `<tool_call>` format, which vLLM's `hermes`
tool parser turns back into OpenAI `tool_calls` (see
[Use the adapter](#use-the-adapter)).

## Try one job

The smallest job, end to end, in curl. Upload with `purpose=fine-tune`:

```bash
curl -H "Authorization: Bearer $SHESHNAG_API_KEY" \
     -F purpose=fine-tune -F file=@train.jsonl \
     $SHESHNAG_URL/v1/files
```
```json
{"id":"file-65b502bc42f8442588255332","object":"file","bytes":136485,"created_at":1795010194,"filename":"train.jsonl","purpose":"fine-tune"}
```

Create the job. Only `model` and `training_file` are required:

```bash
curl -H "Authorization: Bearer $SHESHNAG_API_KEY" -H "Content-Type: application/json" \
     -d '{"model": "qwen2-5-0-5b-instruct-hf", "training_file": "file-65b502bc42f8442588255332", "suffix": "weather"}' \
     $SHESHNAG_URL/v1/fine_tuning/jobs
```

The response is the job, starting in `validating_files`:

```json
{"id": "ftjob-7edd5957a68e4283a176c6a5", "object": "fine_tuning.job",
 "model": "qwen2-5-0-5b-instruct-hf",
 "base_model": {"source_ref": "Qwen/Qwen2.5-0.5B-Instruct", "revision": "7ae557604adf67be50417f59c2c2f167def9a775"},
 "hyperparameters": {"n_epochs": 3, "batch_size": 16, "learning_rate": 0.0002, "max_seq_length": 1024},
 "lora": {"rank": 16, "alpha": 32, "dropout": 0.05, "target_modules": "all-linear"},
 "engine": {"name": "llamafactory", "version": "0.9.5"},
 "status": "validating_files", "error": null, ...}
```

Follow it with `GET /v1/fine_tuning/jobs/{id}` and its events, then download
the adapter. The next section does all of it in one script.

## Submit, follow, download

The same flow in Python with `httpx`:

```python
import os, sys, time
import httpx

client = httpx.Client(
    base_url=os.environ["SHESHNAG_URL"],
    headers={"Authorization": f"Bearer {os.environ['SHESHNAG_API_KEY']}"},
    timeout=60,
)

# 1. Upload the dataset (purpose must be "fine-tune").
with open(sys.argv[1], "rb") as fh:
    file_id = client.post("/v1/files", data={"purpose": "fine-tune"},
                          files={"file": (os.path.basename(sys.argv[1]), fh)}).raise_for_status().json()["id"]
print("uploaded", file_id)

# 2. Create the job.
res = client.post("/v1/fine_tuning/jobs", json={
    "model": "qwen2-5-0-5b-instruct-hf",
    "training_file": file_id,
    "suffix": "weather",
    "hyperparameters": {"n_epochs": 3},
})
if res.status_code != 200:
    sys.exit(f"refused: {res.status_code} {res.text}")
job = res.json()
print("created", job["id"], job["status"])

# 3. Follow it: print new events until the job ends.
seen = set()
while True:
    job = client.get(f"/v1/fine_tuning/jobs/{job['id']}").json()
    events = client.get(f"/v1/fine_tuning/jobs/{job['id']}/events", params={"limit": 100}).json()["data"]
    for event in reversed(events):              # newest first -> print oldest first
        if event["id"] not in seen:
            seen.add(event["id"])
            print(f"  [{event['level']}] {event['message']}")
    if job["status"] in ("succeeded", "failed", "cancelled"):
        break
    time.sleep(5)

if job["status"] != "succeeded":
    error = job["error"]
    sys.exit(f"{job['status']}: {error['code']}: {error['message']}")

# 4. Download the adapter.
adapter_file = job["result_files"][0]
with open("adapter.zip", "wb") as fh:
    fh.write(client.get(f"/v1/files/{adapter_file}/content").raise_for_status().content)
print("adapter.zip saved;", job["fine_tuned_model"],
      "trained on", job["base_model"]["source_ref"], "@", job["base_model"]["revision"])
```

Its output for a 120-example tool-calling dataset:

```text
uploaded file-d38c8ada58ac41dc94e28b64
created ftjob-cc1481b2ac294b5ca46a5014 validating_files
  [info] Created fine-tuning job ftjob-cc1481b2ac294b5ca46a5014
  [info] training_file validated: 120 examples, 42148 tokens (tokenizer:Qwen/Qwen2.5-0.5B-Instruct@7ae557604adf67be50417f59c2c2f167def9a775)
  [info] Job queued for training
  [info] Training started on worker worker-6b61849b0ee749f39c3e3085
  [info] Step 5/24, loss=0.1798
  [info] Step 10/24, loss=0.0103
  [info] Step 15/24, loss=0.0031
  [info] Step 20/24, loss=0.0006
  [info] Training succeeded; adapter ftart-6b2f4b7c0b444384859ff0e3 is servable as ft:qwen2-5-0-5b-instruct-hf:weather:cc1481b2ac29
adapter.zip saved; ft:qwen2-5-0-5b-instruct-hf:weather:cc1481b2ac29 trained on Qwen/Qwen2.5-0.5B-Instruct @ 7ae557604adf67be50417f59c2c2f167def9a775
```

A job moves through `validating_files` → `queued` → `running` → `succeeded`,
`failed` or `cancelled`. It waits in `queued` until a training worker with
enough free GPU memory is online, so poll every 5–30 seconds. The other
calls:

| Call | What it does |
|---|---|
| `GET /v1/fine_tuning/jobs?limit=20&after=<id>` | Your jobs, newest first |
| `GET /v1/fine_tuning/jobs/{id}/events?limit=100` | Validation results, one `metrics` event per logged step (`data` holds `loss`, `learning_rate`, `eval_loss` …), warnings, the outcome. Newest first. |
| `GET /v1/fine_tuning/jobs/{id}/artifacts` | The adapter's `sha256`, rank, target modules, the pinned base, the engine image, and final metrics |
| `POST /v1/fine_tuning/jobs/{id}/cancel` | Stops the job; a running engine is stopped within seconds |

The adapter zip holds `adapter_config.json` and `adapter_model.safetensors`
(about 35 MB at rank 16 on the 0.5B base).

### The OpenAI SDK

`client.files.create(..., purpose="fine-tune")`,
`client.fine_tuning.jobs.create(model=..., training_file=..., suffix=...)` and
`client.fine_tuning.jobs.list()` work against `base_url=$SHESHNAG_URL/v1`
(checked with `openai` 3.55). Two things don't carry over:

- **Hyperparameters go in the top-level `hyperparameters` argument**, e.g.
  `hyperparameters={"n_epochs": 3}`. OpenAI's newer nested
  `method={"type": "supervised", "supervised": {"hyperparameters": …}}` is
  refused with a 422, as is `learning_rate_multiplier`: Sheshnag takes an
  absolute `learning_rate`.
- **`lora`** is a Sheshnag extension the SDK has no parameter for; pass it in
  `extra_body`.

Plain HTTP, as above, avoids both.

## Use the adapter

Load it on **the exact base revision the job reports**
(`job["base_model"]["revision"]`). Nothing checks this for you: an adapter on
a different revision of the same model loads without complaint and gives
worse or garbage answers.

**With PEFT:**

```python
import zipfile
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer

BASE = "Qwen/Qwen2.5-0.5B-Instruct"
REVISION = "7ae557604adf67be50417f59c2c2f167def9a775"   # job["base_model"]["revision"]

zipfile.ZipFile("adapter.zip").extractall("adapter")
tokenizer = AutoTokenizer.from_pretrained(BASE, revision=REVISION)
model = AutoModelForCausalLM.from_pretrained(BASE, revision=REVISION, torch_dtype="auto")
model = PeftModel.from_pretrained(model, "adapter")
```

For the tool-calling model above, asked "Is it raining in Kyoto?" with
`tokenizer.apply_chat_template(..., tools=tools)`, it generated:

```text
<tool_call>
{"name": "get_weather", "arguments": {"city": "Kyoto"}}
</tool_call>
```

**With vLLM:** serve the base at the job's revision and name the adapter.
Add the two tool flags if the adapter was trained on tool calls:

```bash
vllm serve Qwen/Qwen2.5-0.5B-Instruct \
    --revision 7ae557604adf67be50417f59c2c2f167def9a775 \
    --enable-lora --lora-modules weather=./adapter --max-lora-rank 64 \
    --enable-auto-tool-choice --tool-call-parser hermes
```

Then request `"model": "weather"`. With `tools` in the request, the reply
comes back as parsed OpenAI `tool_calls`. On held-out questions the adapter
above returned:

```text
Is it raining in Kyoto right now?                    -> get_weather {"city": "Kyoto"}
I need 75 GBP converted to INR.                      -> convert_currency {"amount": 75, "from": "GBP", "to": "INR"}
What's the weather like in Reykjavik and in Nairobi? -> get_weather {"city": "Reykjavik"}, get_weather {"city": "Nairobi"}
```

`--max-lora-rank` must be at least the adapter's rank; 64 covers every rank
Sheshnag trains.

Requesting a finetuned model by its `fine_tuned_model` name in
`POST /v1/batches` is not supported yet.

## Limits

| Field | Allowed | Default |
|---|---|---|
| `model` | a trainable base (see [What you need](#what-you-need)) | — |
| `training_file`, `validation_file` | ids of files you uploaded with `purpose=fine-tune` | — |
| `hyperparameters.n_epochs` | 1–20 | 3 |
| `hyperparameters.batch_size` | 1–256 (effective batch) | 16 |
| `hyperparameters.learning_rate` | > 0, ≤ 0.01 | 0.0002 |
| `hyperparameters.max_seq_length` | 128–8192 | 1024 |
| `lora.rank` | 8, 16, 32 or 64 | 16 |
| `lora.alpha` | 1–512 | 32 |
| `lora.dropout` | 0 ≤ x < 1 | 0.05 |
| `lora.target_modules` | `"all-linear"`, or a list from `q_proj k_proj v_proj o_proj gate_proj up_proj down_proj` | `"all-linear"` |
| `suffix` | lowercase letters, digits, `-`; up to 40 | none |
| `seed` | any integer | 42 |
| `method.type` | `"supervised"` | `"supervised"` |
| `organization_id` | an org you belong to | your only org; none if you're in several |

**Unknown keys are refused (422)**, including typos. A misspelt `lora_rnk`
would otherwise train at the default rank and report success.

Ranks stop at 64 because serving starts vLLM with `--max-lora-rank 64`, and a
larger adapter stops the whole server from starting, not just itself. The
target modules are the ones verified to serve.

## When it fails

A refused request gets an HTTP error straight away. Everything after that
lands in the job's `error`: `{"code", "param", "message"}`, and for a file
that failed validation also `details` (up to 100 per-line problems, each with
`line`, `code`, `field`, `message` and `hint`) and `total_errors`, the real
count.

### Refused at create

| Response | Cause | Fix |
|---|---|---|
| 422 `rank must be one of [8, 16, 32, 64] — serving starts vLLM with --max-lora-rank 64 …` | rank outside the allowed set | use 8, 16, 32 or 64 |
| 422 `target_modules [...] are not servable` | modules outside the projection set | use `"all-linear"` or the listed modules |
| 422 `Extra inputs are not permitted` | an unknown or misspelt key | check the name against [Limits](#limits) |
| 400 `model: '…' is not a finetune-eligible catalogue entry` | not a trainable base | use an id from [What you need](#what-you-need) |
| 400 `model: catalogue entry '…' has no chat template configured … Finetunable bases: …` | the base can't be trained yet | use one of the bases named |
| 400 `model: … is pinned to 'main', not a commit SHA` | the catalogue entry isn't pinned | ask the deployment's admin |
| 400 `training_file: file '…' was uploaded with purpose 'batch'` | wrong upload purpose | upload again with `purpose=fine-tune` |
| 400 / 403 `training_file: file '…' not found` / `you don't own file` | wrong id, or someone else's file | use the id from your own upload |
| 400 / 403 `organization_id: …` | org doesn't exist, or you aren't a member | name one of your orgs, or leave it out |

### The dataset failed validation

The job ends `failed` with `error.code` `invalid_training_file` (or
`invalid_validation_file`). Its `message` is the first problem and its fix,
for example
`line 1: prompt/completion pairs are not supported — fix: convert each line to {"messages": …} (and 11 more)`.
Read `details` for the rest. The codes:

| `details[].code` | Fix |
|---|---|
| `wrong_format` | prompt/completion, Alpaca or ShareGPT `conversations` lines: convert to `messages`, as the hint shows |
| `json_array`, `invalid_json`, `not_object` | one complete JSON object per line, double quotes, no pretty-printing |
| `not_utf8`, `file_too_large`, `empty_file`, `too_many_examples`, `too_few_examples` | re-save as UTF-8; ≤ 100 MB; 10–50,000 examples |
| `missing_messages`, `too_few_messages`, `invalid_message` | every line needs a `messages` array of at least two turn objects |
| `invalid_role` | roles are `system`, `user`, `assistant`, `tool`; the hint names the right one for `human`, `gpt`, `function` … |
| `misplaced_system` | move the system turn to the start |
| `roles_not_alternating` | merge consecutive user turns, and consecutive assistant turns |
| `no_assistant`, `last_not_assistant` | end each example with the assistant reply |
| `empty_content`, `invalid_content` | non-empty string content; content-part arrays (images, audio) aren't supported |
| `unknown_fields` | only `messages` and `tools` at the top; only the fields each role takes in a turn |
| `invalid_tools`, `invalid_tool`, `duplicate_tool` | `tools` is a list of `{"type": "function", "function": {"name", "parameters"}}`, each name once |
| `invalid_tool_calls`, `invalid_tool_call`, `missing_tool_call_id`, `duplicate_tool_call_id` | each call is `{"id", "type": "function", "function": {"name", "arguments"}}` with its own id |
| `undeclared_function` | declare the function in that line's `tools`, or fix the name |
| `invalid_arguments` | `arguments` is a JSON object **as a string**: `json.dumps(args)`, `"{}"` for none |
| `text_with_tool_calls` | set `content` to `null` on a turn that calls tools |
| `misplaced_tool`, `unknown_tool_call_id`, `unanswered_tool_call` | answer each call once, right after the turn that made it, with its `tool_call_id` |
| `tools_unsupported_base` | tool datasets train only on `qwen2-5-0-5b-instruct-hf` |
| `file_not_found` | upload the file again |

`validation_error` means validation itself crashed; resubmit the job.

### Training failed

The training worker classifies what went wrong. "Retried" failures put the
job back in the queue on their own, up to 3 attempts in all, and the events
say so; a retry starts from step 0. The rest fail at once, because they
would fail the same way again.

| `error.code` | Retried | Fix |
|---|---|---|
| `gpu_out_of_memory` | yes | the worker's GPU is shared; if it keeps failing, lower `max_seq_length` or `lora.rank` |
| `no_trainable_examples` | no | nothing survived the chat template and cutoff; check the last turn is the assistant's and examples fit `max_seq_length` |
| `invalid_dataset` | no | a line couldn't be converted for training; validate the file again |
| `runner_lost` | no | the training worker stopped responding for 10 minutes; resubmit. A job is never restarted from step 0 without telling you. |
| `unservable_adapter` | no | the adapter wouldn't load on vLLM; a platform bug, report it with the job id |
| `runner_config_rejected`, `unsupported_job`, `base_revision_missing`, `base_model_unavailable` | no | a platform or catalogue problem; report it with the job id |
| `network_error`, `worker_disk_full`, `engine_not_started`, `engine_killed`, `dataset_download_failed`, `adapter_missing`, `adapter_upload_failed`, `runner_error`, `training_failed` | yes | a worker problem; if the job fails after 3 attempts, report it with the job id |

## See also

- [Finetuning contract](reference/finetune-contract.md): statuses, tables,
  the worker protocol and the serving limits in full
- [Run your prompts](using-sheshnag.md): the batch API, same key and same
  `/v1/files`
