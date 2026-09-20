# Match the runtime to your machine

**Who this is for:** you have installed the daemon (see [Lend your GPU](provider.md)) and
want it to do useful work rather than sit idle or fail jobs.

The installer sets up Ollama and assumes a single NVIDIA GPU. That is the right default for
most machines and the wrong one for several. This page is how to tell which you have.

---

## Start here

| Your machine | Runtime | Expect |
|---|---|---|
| One NVIDIA GPU, 8 GB or more | **Ollama** — the default, nothing to do | Jobs for any model that fits the card |
| One NVIDIA GPU, 24 GB or more, and you want throughput | **vLLM** | Higher tokens/sec under load; you run the server |
| Several GPUs | **Ollama**, and read [Several GPUs](#several-gpus) | Less than you expect — see below |
| Small GPU, plenty of system RAM | **llama.cpp** | Models far larger than your card, slowly |
| No GPU, 32 GB RAM or more | **llama.cpp** | Small models on CPU; tens of seconds per prompt |
| AMD GPU | **Ollama**, installed by hand | See [AMD](#amd-gpus) |
| Apple Silicon | Not supported by the installer | The installer exits on non-Linux |

---

## The rule the scheduler applies

Before tuning anything, know how work is handed out. **The fit rule depends on the runtime**,
because what a runtime can do with your hardware differs:

| Runtime | A batch is offered when |
|---|---|
| Ollama, vLLM | `largest single card >= the model's footprint` |
| llama.cpp | `largest single card + usable system RAM >= the model's footprint` |

Note *largest single card*, never the sum: neither Ollama nor vLLM splits a model across
cards unless told to, so a box with 2 × 12 GB is not offered a 20 GB model.

Only llama.cpp counts your RAM, because only llama.cpp lets you declare the split with
`--n-gpu-layers`. Ollama will also spill into RAM, but it decides that itself at load time
and does it silently, so the platform cannot promise a job will fit.

**Usable RAM is free RAM minus headroom** — the larger of 4 GB or 20% of the total, kept for
the OS and anything else on the machine. If the daemon cannot read free RAM it counts none,
so a llama.cpp worker that reports no RAM figure is treated as VRAM-only.

A worker is also skipped entirely until its first heartbeat lands, which takes about thirty
seconds after startup. A machine that has just registered and received nothing is normal.

---

## Getting the runtimes

Sheshnag does not package, build or version inference runtimes, and this page deliberately
carries no installation steps for them — upstream changes theirs faster than we could track
it, and a stale copy here is worse than a link.

| Runtime | Install from |
|---|---|
| Ollama | [ollama.com/download](https://ollama.com/download) — or let the Sheshnag installer fetch it |
| vLLM | [vLLM installation guide](https://docs.vllm.ai/en/latest/getting_started/installation.html) |
| llama.cpp | [ggml-org/llama.cpp](https://github.com/ggml-org/llama.cpp) · [build guide](https://github.com/ggml-org/llama.cpp/blob/master/docs/build.md) |

Ollama is the exception only because the installer can fetch a prebuilt binary into your home
directory. For vLLM and llama.cpp, **you install it, you start it, and you keep it running**;
the daemon attaches to a URL and does nothing else.

What follows is how to *configure* a runtime you already have, which is the part Sheshnag
knows about.

---

## Ollama hygiene

Ollama is the default and the installer provisions it. Out of the box it is tuned for one
person chatting, not for a batch platform. Four environment variables change that, and the
installer sets none of them.

They belong to the **Ollama server**, not to the daemon. Put them in the Ollama unit's
environment, not in `config.yaml`:

| Variable | Set to | Why |
|---|---|---|
| `OLLAMA_NUM_PARALLEL` | `4`–`8` | Decode slots per model. Without this, concurrent prompts queue instead of batching, and throughput barely moves. |
| `OLLAMA_KEEP_ALIVE` | `-1` | Default evicts an idle model after ~5 minutes. A batch platform then pays a full reload on the next job. |
| `OLLAMA_FLASH_ATTENTION` | `1` | Smaller KV cache, so more slots fit in VRAM. |
| `OLLAMA_KV_CACHE_TYPE` | `q8_0` | Compresses the KV cache further. Worth it on a card under 16 GB. |

If the installer created the Ollama unit for you, edit it with:

```
systemctl --user edit ollama
```

and add an `[Service]` section with one `Environment=` line per variable, then:

```
systemctl --user restart ollama
```

### Match the daemon to the server

`max_concurrent_prompts` in `config.yaml` decides how many prompts the daemon sends at once.
It defaults to `8`. Sending 8 into a server with 1 decode slot does not make anything faster
— it makes seven prompts wait, and it inflates the per-prompt latency the platform records.

**Set `max_concurrent_prompts` to the same number as `OLLAMA_NUM_PARALLEL`.** Keep both well
under `OLLAMA_MAX_QUEUE` (default 512), or the server returns 503 under load.

---

## vLLM hygiene

vLLM is worth the extra work on a large card under sustained load. It is not worth it on a
12 GB laptop GPU, and it cannot fetch models on demand.

**Everything is yours to run.** The installer does nothing for vLLM —
[install it yourself](https://docs.vllm.ai/en/latest/getting_started/installation.html),
start the server, and whatever it is serving when it starts is the complete set of models
that worker can ever run.

1. Start the server before the daemon:

    ```
    vllm serve <model> --port 8100 --gpu-memory-utilization 0.90
    ```

2. Point the daemon at it in `config.yaml`:

    ```yaml
    runtime: "vllm"
    vllm_url: "http://localhost:8100"
    ```

3. Restart the daemon.

**Flags worth setting:**

| Flag | Why |
|---|---|
| `--gpu-memory-utilization` | Defaults to `0.90`. Lower it if anything else shares the card. |
| `--max-model-len` | Caps the context vLLM reserves KV cache for. Leaving it at the model maximum can consume most of the card before a single request arrives. |
| `--served-model-name` | Gives the model a short name. The daemon reports both this and the repo id, so either matches. |

Leave `hf_hub_cache` alone unless your cache is in an unusual place — the daemon auto-detects
`$HF_HUB_CACHE`, `$HF_HOME/hub`, then `~/.cache/huggingface/hub`, and reads it to identify
what you are serving.

## llama.cpp hygiene

llama.cpp is the only runtime that can serve a model larger than your VRAM. It is also the
only one where a single wrong flag produces a worker that looks perfectly healthy and is
never given work.

**Everything is yours to run**, as with vLLM. The daemon attaches to a `llama-server` you
started and never launches, restarts or tunes one. Get it from
[ggml-org/llama.cpp](https://github.com/ggml-org/llama.cpp); their
[build guide](https://github.com/ggml-org/llama.cpp/blob/master/docs/build.md) covers the
CPU, CUDA, ROCm and Metal builds, and which one you want depends on the hardware sections
below.

### The `--alias` is a contract, not a label

`llama-server` reports whatever `--alias` says, and the platform dispatches by that name. Get
it wrong and your worker registers, passes its health check, shows **online** with a model
listed — and matches nothing in the catalogue, forever.

Take the name from the catalogue entry you intend to serve and pass it exactly:

```
llama-server -m /path/to/model.gguf \
  --alias qwen36-27b-q4kxl \
  --host 127.0.0.1 --port 8080 \
  --ctx-size 4096 --parallel 1
```

Then point the daemon at it in `config.yaml`:

```yaml
runtime: "llamacpp"
llamacpp_url: "http://127.0.0.1:8080"
```

Confirm the name the server is actually using before you start the daemon:

```
curl -s localhost:8080/v1/models | grep -o '"id":"[^"]*"'
```

### Flags worth setting

| Flag | Why |
|---|---|
| `--alias` | The dispatch name. See above — this is the one that silently costs you all your work. |
| `--n-gpu-layers` | How many layers go on the GPU; the rest stream from RAM. `0` is pure CPU, a high number offloads everything that fits. Raise it until the GPU is nearly full. |
| `--ctx-size` | Context per slot. Larger costs RAM for little benefit on batch work. |
| `--parallel` | Decode slots. Match `max_concurrent_prompts` to it — see below. |
| `--embeddings` | Required for embedding models. Without it `/v1/embeddings` returns **501** and every embedding row fails. |

### Tuning `--n-gpu-layers`

This is the lever that decides whether a hybrid machine is useful or merely functional, and
the platform cannot set it for you.

- **No GPU:** leave it unset.
- **Small card (2–6 GB):** start low, raise until `nvidia-smi` shows the card nearly full
  during a run. Every layer moved to the GPU is a real speedup.
- **Card large enough to hold the model:** offload everything and use llama.cpp only if you
  want GGUF specifically; Ollama is less work for the same result.

### Match the daemon to the slots

`llama-server` reports its slot count, and the daemon logs it at startup:

```
llama-server b10759 — 1 slot(s), model /path/to/model.gguf
```

Set `max_concurrent_prompts` to that number. Sending more only queues them and inflates the
per-prompt latency the platform records against you.

### Raise the timeout

`inference_timeout` defaults to 300 seconds. CPU and hybrid generation is measured in tens of
seconds per prompt and a long one will exceed that. Raise it well past your slowest expected
prompt — a job that times out is requeued elsewhere and counts an attempt against a cap of
three.

---

## Running more than one

A machine can drive any combination:

```yaml
runtime: [vllm, ollama, llamacpp]
```

Each registers separately with its own model list and its own fit rule, and a job is routed
to whichever runtime hosts the model. A runtime that is down registers as unavailable and is
skipped; the others keep working.

The combination worth knowing: **Ollama for what fits the card, llama.cpp for what does
not.** One machine then covers both ends of the catalogue instead of being capped by its
VRAM.

---

## Hardware classes

### Plenty of VRAM

**24 GB or more on one card.** The most useful hardware on the platform: it can be offered
every model in the catalogue.

Run Ollama with the four variables above and `max_concurrent_prompts` matched. Move to vLLM
if the machine is dedicated and you want maximum throughput.

### Modest VRAM

**8–16 GB.** The common case, and genuinely useful — most catalogue models are quantised to
fit here.

Ollama, the four variables, and `OLLAMA_KV_CACHE_TYPE=q8_0` in particular: on a 12 GB card the
KV cache is what decides whether you get four concurrent slots or one.

### Small VRAM, plenty of RAM

**A 2–6 GB card with 64 GB or more of system RAM.** This is the case llama.cpp exists for: it
puts as many layers on the GPU as fit and streams the rest from system RAM, so a card that
cannot hold a model can still serve it.

A 2 GB card beside 128 GB of RAM is offered a 25 GB model. The same machine on Ollama is
capped at 2 GB.

Set it up with [llama.cpp](#llamacpp-hygiene) below. The scheduler applies the hybrid rule
only to llama.cpp — Ollama decides the GPU/RAM split itself at load time and spills silently,
so the platform cannot promise a job will fit there.

### No GPU

**A CPU-only machine can take work**, through llama.cpp, if it has the RAM. There is no GPU
path to configure and nothing about the setup differs.

**Size the model against your RAM, not against the disk file.** A 27B Q4 GGUF is 16.4 GB on
disk and needs about **25 GB** resident — the overhead is roughly half again, and it barely
moves with context length. The platform reserves the larger of 4 GB or 20% of your RAM for
the OS and other tenants, so:

| Your RAM | Reserved | Usable | Realistic |
|---|---|---|---|
| 32 GB | 6.4 GB | 23.6 GB | 3B–8B comfortably; a 27B Q4 does **not** fit |
| 64 GB | 12.8 GB | 51.2 GB | up to ~32B Q4 |
| 128 GB | 25.6 GB | 102.4 GB | anything in the catalogue |

Expect **tens of seconds per prompt**, not hundreds of milliseconds. That is the trade: the
machine is slow but it is not idle.

### Several GPUs

**The scheduler fits a model on your largest single card, never on the sum.** A box with
2 × 12 GB is not offered a 20 GB model, because that model fits on neither card.

This is deliberate: neither Ollama nor vLLM splits a model across cards without being told
to, and a job dispatched on the strength of a 24 GB total would fail on arrival.

To use both cards, run vLLM with tensor parallelism across them and let it present one larger
logical device:

```
vllm serve <model> --tensor-parallel-size 2 --port 8100
```

### AMD GPUs

Detected, and usable, but not automatically:

- **The installer fetches an x86-64 Ollama build regardless of your host**, so an AMD or
  arm64 machine gets no working runtime from it. Install Ollama's ROCm build yourself first;
  the installer then detects it on `PATH` and skips the download.
- `/dev/kfd` normally requires membership of the `video` or `render` group. Adding yourself
  needs an admin, and without it everything installs cleanly and then finds no usable GPU.
- vLLM on AMD needs a ROCm build. The default PyPI wheels are CUDA-only and will not see the
  card. The daemon warns about this at startup rather than at the first prompt.

### Apple Silicon

The daemon detects Metal, but `install.sh` exits on anything that is not Linux, so there is
no supported install path.

---

## Advertise honestly

If the daemon cannot detect your GPU you can declare a capacity by hand:

```
DAEMON_VRAM_GB=24
```

**Nothing validates this number.** Claiming more than the card holds means the scheduler
dispatches models that cannot run, and those jobs fail on your machine and are requeued
elsewhere with an attempt burned. Under-claiming costs you nothing but work you would have
received.

If you are unsure, declare less.

---

## Check it worked

```
systemctl --user status gpu-daemon
journalctl --user -u gpu-daemon -f
```

A healthy worker logs its runtime and model list at startup, then a heartbeat on a regular
interval. In the Provider portal it shows as **online** with its models listed.

A worker that is online and never receives a job is usually one of:

| Symptom | Cause |
|---|---|
| No models listed | The runtime is reachable but serving nothing |
| One model listed, never any jobs | **On llama.cpp, almost always the `--alias`** — the served name does not match a catalogue entry. Compare `curl localhost:8080/v1/models` against the entry you meant to serve. |
| Models listed, no jobs | No catalogue model fits — check your largest card, and on llama.cpp your free RAM after headroom |
| Jobs fail immediately | Advertised VRAM exceeds what the card holds |
| Every embedding row fails with 501 | `llama-server` was started without `--embeddings` |
| Jobs start, then time out | `inference_timeout` is below what CPU or hybrid generation needs |

Per-setting detail is in [Configuration](reference/configuration.md); the full daemon
surface is in [Daemon internals](reference/daemon.md).
