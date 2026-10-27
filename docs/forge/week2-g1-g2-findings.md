# Week 2 — G1 (serve the week-1 adapter) and G2 (engine bake-off)
**Manan Ghonia — Sheshnag Forge — 2026-10-21**

Everything below was run on watchtower; raw evidence lives in
`/srv/forge/runs/manan/week2/` (`g1/`, `logs/`, `axolotl/`, `llamafactory/`, `failtests/`).

**Conditions that shaped every number:** AC's `Qwen3.8-27B-FP8` vLLM held ~43.7 of 49 GB the
whole session, leaving **~4.8 GB**. All runs were sized to fit in that gap; nothing of AC's was
touched. One run per engine, 0.5B model — treat timings as ±10%, not as a benchmark.

---

## G1 — Serve the week-1 adapter

Ved's attempt on 2026-08-25 (`week1-vllm` container) failed with `vllm: command not found`: the
axolotl image has no vLLM. A separate pinned image is needed (and inside a container the path
is `/work/...`, not `/srv/forge/...`).

| Check | Result |
|---|---|
| vLLM starts with `--enable-lora` + week-1 adapter | ✅ `vllm/vllm-openai` v0.31.0, `--max-lora-rank 16`, 0.06 GPU-mem budget (2.84 GiB; 1.65 GiB KV) |
| curl `/v1/chat/completions` with `model=week1` | ✅ `"Hello! How can I assist you today?"` — `g1/curl_week1.json` |
| Same prompt, base vs adapter, both captured | ✅ week-1's 5 prompts, greedy — `g1/compare_base_vs_week1.{txt,json}`; 0/5 identical |
| Constraints written down | ✅ below |

**Fidelity:** vLLM's adapter output matches week-1's PEFT output where greedy decoding stays in
lockstep (e.g. "Big Blue / Big Red / Big Yellow" verbatim), and diverges mid-answer elsewhere —
but the *base* model diverges from HF at the same rate (1/5 exact each). That is bf16 kernel
numerics flipping a greedy token, not the LoRA being mis-applied.

### Constraints (spec input)

**1. Max rank.** `--max-lora-rank` must be ≥ the adapter's `r`. Under-sizing gives a clear message —
`ValueError: LoRA rank 16 is greater than max_lora_rank 8.` — but with `--lora-modules` at startup,
**the whole server fails to start**, not just that adapter. One bad adapter = zero serving.
→ The runner must cap `r` at a platform max, and the worker must check `adapter_config.json["r"]`
before handing an adapter to vLLM.

**2. Base-model match. vLLM checks nothing.**
- Shape-different base (`Qwen3-0.6B`): server dies with
  `The size of tensor a (3072) must match the size of tensor b (4864)` — never mentions base models.
- **Shape-identical base (`Qwen2.5-0.5B`, non-Instruct): loads silently, HTTP 200, `/v1/models`
  reports `parent: Qwen/Qwen2.5-0.5B`, and serves garbage** (`"You are a helpful assistant.iente\niente\n…"`).

→ Base identity must be enforced by the platform, by catalogue digest, before loading.
`base_model_name_or_path` in `adapter_config.json` is a free-text repo name (no revision), so it
is not sufficient on its own.

**3. Target-module verdict: `all-linear` is servable — for Qwen2 dense.** It resolves to
`q,k,v,o,gate,up,down_proj`; vLLM 0.31.0 loaded it with no unsupported-module warnings.
All three trainers (TRL, Axolotl, LLaMA-Factory) produced the same 7-module set, none with
`lm_head`, `embed_tokens` or `modules_to_save`. **Not tested:** embedding/`lm_head` targets,
`modules_to_save`, MoE or non-Qwen2 architectures — the runner should refuse those until they are.

**Other things serving will trip on**
- vLLM applies the model's `generation_config.json` as request defaults
  (`repetition_penalty 1.1, temperature 0.7, top_p 0.8, top_k 20` for Qwen2.5) when the client omits them.
- Adapter files are written owner-only: Axolotl `adapter_model.safetensors` is `0600`, TRL `0640`.
  A serving worker under another uid can't read them — the runner must set permissions explicitly.

---

## G2 — Engine bake-off

Both engines got week 1's dataset (byte-identical JSONL exported once from no_robots[:500],
90/10, seed 42 — `data/prep.py`, 132,626 train tokens) and week 1's settings: Qwen2.5-0.5B-Instruct,
LoRA r16/α32/dropout 0.05 all-linear, 1 epoch, lr 2e-4 linear no warmup, AdamW fused, bf16,
grad checkpointing, seq 1024, no packing, loss on all tokens.

**One forced deviation:** micro-batch 2 × grad-accum 8 OOMs Axolotl at step 2 (needed 1.02 GiB more
for fp32 logits over the 152k vocab). Both engines therefore ran **micro-batch 1 × grad-accum 16**
— same effective batch of 16.

### Numbers

| | Axolotl | LLaMA-Factory | (week-1 TRL, bs2×8) |
|---|---|---|---|
| Completed | ✅ (28 steps) | ✅ (29 steps) | 29 steps |
| `train_runtime` | 44.2 s | 41.5 s | 30.4 s |
| End-to-end wall-clock ¹ | 65.0 s | 66.1 s | 43 s |
| Peak VRAM, host-side ² | **3.87 GB** | 4.70 GB | — |
| Tokens/s ³ | 3,000 | 3,190 | — |
| Held-out loss, assistant tokens ⁴ | 2.4216 | 2.4201 | 2.4219 (base 2.5194) |
| Held-out loss, all tokens ⁴ | 2.6549 | 2.3263 | 2.3254 (base 2.8822) |
| Eval ran during training | ❌ silently skipped | ✅ eval_loss 2.314 | ✅ 2.318 |
| Adapter servable on vLLM | ✅ | ✅ (both loaded together) | ✅ |

¹ Axolotl via `docker exec` into a running container; LLaMA-Factory includes a `docker run` cold start.
² `max(nvidia-smi used) − baseline`, sampled every 0.5 s; includes CUDA context.
³ Engine-independent: 132,626 templated train tokens ÷ `train_runtime`.
⁴ One script (`heldout_eval.py`) scores every adapter on the same 50 rows with the same masking.
Base reproduces week 1's step-0 eval_loss (2.882 vs 2.896), which validates it.

**Reading it:** quality is a tie; on assistant tokens all three are within 0.002. Axolotl's higher
all-token loss (and its flat training loss, 2.51 → 2.56) is not a worse model: even with
`roles_to_train: [system, user, assistant]` it trains on 103k of 128k tokens, skipping the injected
system prompt and template markers. Those are trivially memorisable and inflate TRL's apparent drop.
Speed is a tie within single-run noise. Axolotl uses ~0.8 GB less VRAM (it auto-enabled its LoRA kernels).

### Supervisor-facing qualities (the reason the runner cares)

**Progress output**
- **LLaMA-Factory:** writes `trainer_log.jsonl` — one JSON object per log step with
  `current_steps, total_steps, loss, lr, epoch, percentage, elapsed_time, remaining_time`. A supervisor
  can tail it directly.
- **Axolotl:** stdout only, as Python `dict` reprs (single quotes, numbers stringified:
  `{'loss': '2.557', …}`) interleaved with `\r` tqdm bars. `trainer_state.json` appears only at
  checkpoints. Parseable with `ast.literal_eval` + regex, but fragile. The upside is richer content
  (memory and tokens/s per log line).

**Failure messages** (`failtests/`)

| Mistake | Axolotl | LLaMA-Factory |
|---|---|---|
| Typo'd key `lora_rnk: 16` | **`preprocess` exits 0, no warning** — key silently dropped (see below) | `Some keys are not used by the HfArgumentParser: ['lora_rnk']` |
| Missing dataset | clear: names the path | clear: `Undefined dataset week1_trian in dataset_info.json.` |
| Eval configured but not run | silent — a boundary bug (see below) | n/a — ran |

**Root causes (read from Axolotl source at the image's commit `e198b1d8`, still present on
`main` @ `dfb9cb36`, 2026-10-21):**

- **Unknown keys:** `AxolotlInputConfig.model_config = {"populate_by_name": True}` — no
  `extra="forbid"`, so pydantic's default `extra="ignore"` drops any key it doesn't know. The
  typo leaves `lora_r = None`, and `loaders/adapter.py` passes `LoraConfig(r=cfg.lora_r)` explicitly,
  overriding PEFT's default of 8. PEFT 0.20's `update_layer` then evaluates `if r <= 0`, which should
  raise a bare `TypeError` (`None <= 0`) at model load — after preprocessing reported success.
  *Prediction; confirmed by the follow-up run if marked ✅ below.*
- **Skipped eval:** `utils/config/__init__.py` computes
  `eval_steps = 1.0 / (evals_per_epoch * num_epochs)`, then handles `< 1.0` (set it) and `> 1`
  (warn, skip). **Exactly 1.0 — i.e. `evals_per_epoch: 1` on a 1-epoch run — matches neither
  branch**, so no eval schedule is set, nothing is logged, and HF's default `eval_strategy="no"`
  applies. Workaround: set `eval_strategy: epoch` explicitly. The same fall-through exists for
  `saves_per_epoch`, but saving is masked by a default `save_strategy: epoch`.

Implication for the runner: with Axolotl it must validate its own generated config against a
key whitelist and never use `evals_per_epoch` — i.e. re-implement checks LLaMA-Factory does itself.

**Config legibility:** both are flat YAML that maps cleanly to week-1's TRL arguments. Axolotl's
dataset block is richer (per-dataset roles, EOS handling). LLaMA-Factory needs a side file
(`dataset_info.json`) for dataset registration — one more file for the runner to generate, but explicit.

### DPO / ORPO (from the installed source, not just docs)

- **Axolotl:** `rl: dpo | ipo | orpo | kto | simpo | grpo | gdpo` (`utils/schemas/enums.py`
  `RLType`); examples in `examples/qwen2/dpo.yaml`, `examples/mistral/orpo/`.
- **LLaMA-Factory:** `stage: dpo` plus `pref_loss: sigmoid (DPO) | orpo | simpo | ipo | hinge | kto_pair`
  (ORPO/SimPO drop the reference model automatically), and `stage: kto`
  (`hparams/finetuning_args.py`). Example `examples/train_lora/qwen3_lora_dpo.yaml`:
  ```yaml
  stage: dpo
  finetuning_type: lora
  lora_target: all
  pref_beta: 0.1
  pref_loss: sigmoid   # or orpo, simpo
  dataset: dpo_en_demo
  ```

### Winner: **LLaMA-Factory** (overturns the spec's Axolotl prior; Ved co-signed 2026-10-26)

On the raw numbers (quality, speed) it is a tie. Axolotl wins VRAM. The decision therefore rests on
how each engine behaves **as a subprocess under a supervisor**, which is what the runner actually
needs, and there LLaMA-Factory wins all three: machine-readable progress, strict config validation,
and no silent skipping.

**What would flip it:**
- **LLaMA-Factory's pinned stack is old:** torch 2.6.0+cu124, last release 0.9.5 (2026-05-30).
  Mitigated on paper: its transformers 5.6.0 ships `qwen3`, `qwen3_5`, `qwen3_5_moe` architectures
  and 0.9.5 ships `qwen3`, `qwen3_5`, `qwen3_6` templates. A 10-step train on Qwen3-0.6B and
  Qwen3.5-0.8B is the remaining proof (follow-up step 3). There is no `qwen3_6` *architecture*
  in transformers 5.6.0 — the catalogue's `qwen3.6:27b` needs its `config.json` `model_type` checked.
- **Axolotl's silent paths are now explained** (above) and have workarounds, but they are the
  runner's burden to carry; an Axolotl callback plugin could also emit JSONL progress.

Ved co-signed 2026-10-26. The pinned spec engine is `hiyouga/llamafactory@sha256:c5e769e0…` (0.9.5); see the table below.

### Follow-up results (2026-10-26, `logs/followup.log`, `logs/*.summary.json`)

| Check | Result |
|---|---|
| Axolotl, typo'd `lora_rnk`, full train | ✅ prediction confirmed: preprocess passes, then `TypeError: '<=' not supported between instances of 'NoneType' and 'int'` at model load (exit 1, 24 s). Error never names the key. |
| Axolotl with `eval_strategy: epoch` | ✅ workaround works: eval runs (`eval_loss` 2.757 at start → 2.594 at end), 57.8 s, peak +3.84 GB |
| LLaMA-Factory 0.9.5, `Qwen/Qwen3-0.6B`, 10 steps | ✅ trains, `train_loss` 2.651, 43.1 s, peak +4.22 GB |
| LLaMA-Factory 0.9.5, `Qwen/Qwen3.5-0.8B`, 10 steps | ✅ at `cutoff_len: 512` (`train_loss` 2.361, 101 s, peak +4.55 GB). At 1024 it loaded the model and then OOMed on a 1.02 GiB logits alloc with only ~4.8 GB free (a box limit, not an engine one). Loads as `qwen3_5` (text + vision towers). |

All three things that could have flipped the verdict came back in LLaMA-Factory's favour or neutral; the
old-stack risk is retired for Qwen3 / Qwen3.5.

### Pinned versions

| | Image digest | Stack |
|---|---|---|
| vLLM | `vllm/vllm-openai@sha256:c1c9f6fd5c109ba7f0546a59f5b2f15fb87f64c77782e90a27b648b42a8e67c3` (v0.31.0, 2026-10-04) | vllm 0.31.0 |
| LLaMA-Factory | `hiyouga/llamafactory@sha256:c5e769e047a19f9322800c9881a6270557ef0e6682f0145b50b322f8724de6b6` (0.9.5) | llamafactory 0.9.5, transformers 5.6.0, peft 0.18.1, trl 0.24.0, torch 2.6.0+cu124, accelerate 1.11.0, datasets 4.0.0 |
| Axolotl | `axolotlai/axolotl@sha256:0252b145bff4945b76e99aa44af6746ce5f0fc99157bb461314f87b6a7f8bbe4` (main-latest 2026-08-20) | axolotl 0.19.0.dev0 (a dev build, not a release), transformers 5.15.0, peft 0.20.0, trl 1.8.0, torch 2.12.0+cu130, accelerate 1.13.0, datasets 4.8.4 |

vLLM v0.32.0 (2026-10-18) was skipped as too new to pin.

### Open items
- [x] Root-cause Axolotl's skipped eval — boundary bug, above.
- [x] Root-cause Axolotl's silent typo — `extra="ignore"` schema, above.
- [x] GPU confirmation (`g2_followup.sh`, 2026-10-26) — see *Follow-up results* below.
- [x] Ved co-signed the winner (2026-10-26). Spec engine is LLaMA-Factory 0.9.5, pinned by digest below.
- [ ] Disk: the two new images add ~20 GB under my rootless Docker; prune the loser once agreed.
