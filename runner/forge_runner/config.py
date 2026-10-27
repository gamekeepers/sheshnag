"""Job -> LLaMA-Factory config.

forge-runner does not write training code: it writes the engine's YAML and
shells out (the G2 decision). The keys below are the ones the G2 bake-off run
used on watchtower (docs/forge/week2-g1-g2-findings.md), plus model_revision
for the pinned base. LLaMA-Factory rejects keys it does not know, so a typo
here fails the run loudly instead of training with a default.
"""
import json
from pathlib import Path

import yaml

from forge_runner.job import ALL_LINEAR, Job


class UnsupportedJob(ValueError):
    """The job cannot be turned into an engine config. Not retryable."""


# Chat template per base model: it must be the one the base was trained
# with, or the adapter learns the wrong turn markers. Refuse unknown bases
# instead of guessing — a wrong template trains fine and serves garbage.
CHAT_TEMPLATES = {
    "Qwen/Qwen2.5-0.5B-Instruct": "qwen",
    "TinyLlama/TinyLlama-1.1B-Chat-v1.0": "zephyr",
}

# Micro-batch 1 x gradient accumulation = the job's effective batch. G2 found
# micro-batch 2 OOMs a 0.5B model in the ~4.8 GB a shared watchtower leaves
# free; the step count and the maths are the same either way.
MICRO_BATCH = 1
LOGGING_STEPS = 5

_SHAREGPT_TAGS = {
    "role_tag": "role", "content_tag": "content", "user_tag": "user",
    "assistant_tag": "assistant", "system_tag": "system",
}


def _dataset_entry(file_name: str) -> dict:
    return {"file_name": file_name, "formatting": "sharegpt",
            "columns": {"messages": "messages"}, "tags": _SHAREGPT_TAGS}


def build(job: Job, workdir: Path, *, has_validation: bool) -> tuple[dict, dict]:
    """Return (llamafactory config, dataset_info.json) for a job whose data
    sits in workdir/data/{train,validation}.jsonl."""
    if job.method != "supervised":
        raise UnsupportedJob(f"method '{job.method}' is not supported by this runner yet")
    template = CHAT_TEMPLATES.get(job.source_ref)
    if template is None:
        raise UnsupportedJob(
            f"no chat template is configured for base model '{job.source_ref}'; "
            "add it to forge_runner.config.CHAT_TEMPLATES before training on it")

    dataset_info = {"train": _dataset_entry("train.jsonl")}
    config = {
        "model_name_or_path": job.source_ref,
        "stage": "sft",
        "do_train": True,
        "finetuning_type": "lora",
        "lora_rank": job.lora_rank,
        "lora_alpha": job.lora_alpha,
        "lora_dropout": job.lora_dropout,
        "lora_target": "all" if job.target_modules == ALL_LINEAR else job.target_modules,
        "dataset_dir": str(workdir / "data"),
        "dataset": "train",
        "template": template,
        "cutoff_len": job.max_seq_length,
        "overwrite_cache": True,
        "preprocessing_num_workers": 1,
        "output_dir": str(workdir / "out"),
        "overwrite_output_dir": True,
        "logging_steps": LOGGING_STEPS,
        "save_strategy": "no",          # the final adapter is always written; checkpoints come later
        "report_to": "none",
        "per_device_train_batch_size": MICRO_BATCH,
        "gradient_accumulation_steps": max(1, job.batch_size // MICRO_BATCH),
        "learning_rate": job.learning_rate,
        "num_train_epochs": float(job.epochs),
        "lr_scheduler_type": "linear",
        "warmup_ratio": 0.0,
        "optim": "adamw_torch_fused",
        "seed": job.seed,
        "bf16": True,
    }
    if job.source_revision:
        config["model_revision"] = job.source_revision
    if has_validation:
        dataset_info["validation"] = _dataset_entry("validation.jsonl")
        config.update(eval_dataset="validation", eval_strategy="epoch",
                      per_device_eval_batch_size=MICRO_BATCH)
    return config, dataset_info


def write(job: Job, workdir: Path, *, has_validation: bool) -> Path:
    """Write the config and dataset_info.json; return the config path."""
    config, dataset_info = build(job, workdir, has_validation=has_validation)
    (workdir / "data").mkdir(parents=True, exist_ok=True)
    (workdir / "data" / "dataset_info.json").write_text(json.dumps(dataset_info, indent=2))
    path = workdir / "train.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    return path
