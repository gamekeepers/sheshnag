"""
The finetuning job contract — what a finetuning job *is*, written down once
so the API (routers/finetune.py) and the runner (the worker endpoints in
routers/workers.py, and forge-runner behind them) are built against the same
thing. The prose version, with the reasoning, is docs/finetune_contract.md.

Two inputs shaped every constant here:

- **G1 (week 2): serving the week-1 adapter on vLLM 0.31.0.** An adapter you
  cannot route to is not a deliverable, so the job carries exactly the fields
  serving needs, and the platform enforces the limits vLLM itself does not:
    * `r` above the server's `--max-lora-rank` stops the *whole server* from
      starting, not just that adapter -> ranks are capped at MAX_LORA_RANK.
    * vLLM never checks the base model; a shape-identical wrong base loads
      silently and returns garbage with HTTP 200 -> the base is pinned on the
      job (catalogue id + source ref + revision) and checked on the artifact.
    * `all-linear` on Qwen2 dense resolves to the seven projection modules
      below and serves cleanly. Embedding / lm_head targets and
      `modules_to_save` were not tested -> refused until they are.
- **G2 (week 2): engine bake-off.** LLaMA-Factory 0.9.5 won (co-signed
  2026-10-26), pinned by image digest below.
"""

# ─── Job lifecycle (mirrors OpenAI's fine_tuning.job statuses) ─────────────

JOB_STATUSES = ("validating_files", "queued", "running", "succeeded", "failed", "cancelled")
TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})

VALID_TRANSITIONS = {
    "validating_files": ["queued", "failed", "cancelled"],
    "queued":           ["running", "cancelled"],
    "running":          ["succeeded", "failed", "queued", "cancelled"],  # "queued" = requeue
    "succeeded":        [],
    "failed":           [],
    "cancelled":        [],
}


def can_transition(current: str, target: str) -> bool:
    return target in VALID_TRANSITIONS.get(current, [])


# ─── Training method & engine ──────────────────────────────────────────────

# Only SFT today. DPO/ORPO are confirmed available in the chosen engine
# (LLaMA-Factory `stage: dpo` + `pref_loss: sigmoid|orpo|simpo`) — adding
# them is a new value here plus a preference-pair validator, not a new table.
METHODS = ("supervised",)

ENGINE = "llamafactory"
ENGINE_VERSION = "0.9.5"
ENGINE_IMAGE = (
    "hiyouga/llamafactory@sha256:"
    "c5e769e047a19f9322800c9881a6270557ef0e6682f0145b50b322f8724de6b6"
)

# ─── LoRA limits that serving imposes (G1) ─────────────────────────────────

# Serving workers start vLLM with `--max-lora-rank MAX_LORA_RANK`. Every rank
# the API accepts must be <= it, or one adapter takes the server down.
# Powers of two only: vLLM sizes its LoRA buffers for the max rank anyway,
# so odd ranks buy nothing.
ALLOWED_LORA_RANKS = (8, 16, 32, 64)
MAX_LORA_RANK = max(ALLOWED_LORA_RANKS)

# What `all-linear` resolved to for Qwen2 in G1 (and is the same set of names
# for Llama-family models). The only target modules proven servable.
SERVABLE_TARGET_MODULES = frozenset({
    "q_proj", "k_proj", "v_proj", "o_proj",
    "gate_proj", "up_proj", "down_proj",
})
ALL_LINEAR = "all-linear"

# ─── Defaults (week 1's settings, which both G2 engines reproduced) ────────

DEFAULT_HYPERPARAMETERS = {
    "n_epochs": 3,
    "batch_size": 16,          # effective batch; the runner picks micro-batch x grad-accum to fit VRAM
    "learning_rate": 2e-4,
    "max_seq_length": 1024,
}
DEFAULT_LORA = {
    "rank": 16,
    "alpha": 32,
    "dropout": 0.05,
    "target_modules": ALL_LINEAR,
}
DEFAULT_SEED = 42


def adapter_unservable_reasons(adapter_config: dict, *, expected_base_ref: str | None) -> list[str]:
    """Check a trained adapter's `adapter_config.json` against what G1 proved
    vLLM can serve. Returns human-readable reasons; empty list = servable.

    `base_model_name_or_path` is free text with no revision, so a match here
    is necessary but not sufficient — the job's pinned revision is the real
    identity, and serving must load exactly that.
    """
    reasons = []

    if adapter_config.get("peft_type") != "LORA":
        reasons.append(f"peft_type is {adapter_config.get('peft_type')!r}, only 'LORA' is servable")

    r = adapter_config.get("r")
    if not isinstance(r, int) or r < 1 or r > MAX_LORA_RANK:
        reasons.append(f"rank r={r!r} is outside 1..{MAX_LORA_RANK} (serving's --max-lora-rank)")

    targets = adapter_config.get("target_modules")
    if isinstance(targets, str):
        targets = [targets]
    if not targets:
        reasons.append("target_modules is empty")
    else:
        unknown = sorted(set(targets) - SERVABLE_TARGET_MODULES)
        if unknown:
            reasons.append(f"target modules {unknown} are not proven servable "
                           f"(allowed: {sorted(SERVABLE_TARGET_MODULES)})")

    if adapter_config.get("modules_to_save"):
        reasons.append(f"modules_to_save={adapter_config['modules_to_save']!r} is not servable as a LoRA adapter")

    base = adapter_config.get("base_model_name_or_path")
    if expected_base_ref and base != expected_base_ref:
        reasons.append(f"adapter was trained on {base!r}, job's base model is {expected_base_ref!r}")

    return reasons
