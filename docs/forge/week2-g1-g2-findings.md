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

