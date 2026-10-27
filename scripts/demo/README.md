# Finetuning demo: presenter notes

The board's sentence is *"Submit a dataset, get an adapter, and call it,
without ever downloading a file."* This demo shows the first three stages
live on watchtower. It takes about 10 minutes. Run it during office hours
(the box is off after 19:00).

## Before the audience (5 min)

```bash
ssh forge
~/sheshnag-demo/scripts/demo/setup.sh      # fresh backend on 127.0.0.1:8800, fresh DB
```

- **Post in the pod chat** that you are using the GPU for about 3 minutes.
- Setup prints free VRAM. You need **at least 4.7 GB** free, which is what
  AC's 27B vLLM leaves today. If it is lower, someone else is training: wait,
  or show the recording.
- Make the terminal font large, and use a dark background.

## Run it

```bash
~/sheshnag-demo/scripts/demo/demo.sh       # pauses before each step: press Enter
```

| Step | Say | Shows |
|---|---|---|
| 1 | "People upload the wrong format. The usual result is a cryptic crash an hour into training." | 3 wrong formats rejected in seconds, each with a fix |
| 2 | "Same endpoint shape as OpenAI's fine-tuning API, same Sheshnag API key." | `queued`, token counts with the base's own tokenizer at a pinned revision, length histogram |
| 3 | "In week 2 we found that one adapter with too high a rank stops the whole vLLM server from starting. So the contract refuses it up front." | HTTP 422 naming `--max-lora-rank` |
| 4 | "forge-runner is separate from the batch daemon. A training run is not a batch of prompts. It writes the engine config, runs pinned LLaMA-Factory, and streams the loss." | live loss events, then `servable: true` and a `fine_tuned_model` name |
| 5 | "And we call it by name, on the exact base revision it was trained on." | vLLM lists the adapter; the same prompt gives a base answer and an adapter answer |

Step 4 takes about 40 s of training plus a minute of model loading. Step 5
takes about a minute for vLLM to start.

## Be upfront about

- **Step 5 calls vLLM directly**, not through Sheshnag. Calling the adapter
  with a Sheshnag key in `/v1/batches` (stage 4) is weeks 9–11.
- **There is no web UI yet.** Everything is the public API, which is what the
  UI will call.
- **This is a 0.5B model on 450 examples for one epoch**, so the answers differ
  but are not dramatically better. The demo is about the pipeline, not about
  model quality. Held-out eval loss is in step 4's output.

## If something goes wrong

| Symptom | Do |
|---|---|
| Step 4 fails with `gpu_out_of_memory` | Someone took the GPU. That is the error message working. Show `rehearsal.log` |
| vLLM never becomes ready | `docker logs sheshnag-demo-vllm`; usually not enough free VRAM |
| Anything else | `teardown.sh`, then show the recorded run: `less -R /srv/forge/runs/manan/demo/rehearsal.log` |

Afterwards: `~/sheshnag-demo/scripts/demo/teardown.sh`.
