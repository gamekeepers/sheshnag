"""Stands in for `llamafactory-cli train <config>` in tests.

Reads the generated config, then behaves per FAKE_ENGINE_MODE:
  ok     — writes trainer_log.jsonl step by step, the adapter, all_results.json
  oom    — logs a step, then dies the way a CUDA OOM does
  badkey — exits like LLaMA-Factory rejecting an unknown config key
  slow   — logs steps for ~30 s (for cancellation tests)
"""
import json
import os
import sys
import time
from pathlib import Path

import yaml

config = yaml.safe_load(Path(sys.argv[1]).read_text())
out = Path(config["output_dir"])
out.mkdir(parents=True, exist_ok=True)
mode = os.environ.get("FAKE_ENGINE_MODE", "ok")
total = 4

if mode == "badkey":
    print("ValueError: Some keys are not used by the HfArgumentParser: ['lora_rnk']")
    sys.exit(1)

with open(out / "trainer_log.jsonl", "w") as log:
    for step in range(1, total + 1):
        log.write(json.dumps({"current_steps": step, "total_steps": total, "loss": 2.5 - 0.1 * step,
                              "lr": 2e-4, "epoch": step / total, "percentage": 25.0 * step}) + "\n")
        log.flush()
        if mode == "oom":
            print("Traceback (most recent call last):")
            print("torch.OutOfMemoryError: CUDA out of memory. Tried to allocate 1.02 GiB.")
            sys.exit(1)
        time.sleep(10 if mode == "slow" else 0.05)

(out / "adapter_config.json").write_text(json.dumps({
    "peft_type": "LORA", "r": config["lora_rank"], "lora_alpha": config["lora_alpha"],
    "base_model_name_or_path": config["model_name_or_path"],
    "target_modules": ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
}))
(out / "adapter_model.safetensors").write_bytes(b"fake-weights")
(out / "all_results.json").write_text(json.dumps({"epoch": 1.0, "train_loss": 2.3, "train_runtime": 0.2}))
print("***** train metrics *****")
