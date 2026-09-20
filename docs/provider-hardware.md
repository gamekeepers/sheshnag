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
| Small GPU, plenty of system RAM | **Ollama**, capped at your VRAM | Small models only, for now |
| No GPU | Nothing useful yet | The daemon runs and stays idle |
| AMD GPU | **Ollama**, installed by hand | See [AMD](#amd-gpus) |
| Apple Silicon | Not supported by the installer | The installer exits on non-Linux |

---

## The rule the scheduler applies

Before tuning anything, know how work is handed out. A batch is offered to your worker when
**one single GPU** can hold the whole model:

```
largest single card  >=  the model's footprint
```

Not the sum of your cards. Not your VRAM plus your system RAM. The largest card, by itself.

Two consequences catch providers out, and both are covered below: a multi-GPU box is worth
less than its total VRAM suggests, and a box with a small card and 128 GB of RAM is worth
only what the small card holds.

A worker is also skipped entirely until its first heartbeat lands, which takes about thirty
seconds after startup. A machine that has just registered and received nothing is normal.

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

**Everything is yours to run.** The installer does nothing for vLLM. You install it, you
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

### Running both

A machine can drive both runtimes:

```yaml
runtime: [vllm, ollama]
```

Each is registered separately with its own model list, and a job is routed to whichever
runtime hosts the model. Useful when vLLM holds one large model permanently and Ollama covers
everything else on demand.

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

**A 2–6 GB card with 64 GB or more of system RAM.** Today you are limited to models that fit
the card, and your RAM does nothing.

This is a known gap, not a misconfiguration. The platform's scheduler can already express
"this model fits in VRAM **plus** system RAM", but the runtime that would use it — llama.cpp,
which streams the layers that do not fit on the GPU from RAM — is not yet supported.

!!! note "Do not set `runtime: llamacpp`"

    The daemon rejects unknown runtimes at startup and will refuse to start. Support is
    tracked in [issue #133](https://github.com/gamekeepers/sheshnag/issues/133).

Until then, run Ollama and expect small models. Ollama will also silently spill a too-large
model into RAM and run it very slowly, which is why the scheduler does not count your RAM as
capacity for it.

### No GPU

**A CPU-only machine**, however much RAM it has, cannot currently be given work. The daemon
installs, registers, reports no GPU, and idles.

The same llama.cpp work above is what changes this. There is nothing to configure in the
meantime.

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
| Models listed, no jobs | No catalogue model fits your largest card |
| Jobs fail immediately | Advertised VRAM exceeds what the card holds |

Per-setting detail is in [Configuration](reference/configuration.md); the full daemon
surface is in [Daemon internals](reference/daemon.md).
