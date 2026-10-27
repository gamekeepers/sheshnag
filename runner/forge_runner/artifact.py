"""Engine output dir -> the adapter zip the backend accepts.

The backend reads adapter_config.json from the zip itself and judges
servability (docs/finetune_contract.md); the runner only has to ship the
two files and make them readable. G1 found adapters written 0600, which a
serving process under another uid cannot open.
"""
import json
import os
import zipfile
from pathlib import Path

ADAPTER_FILES = ("adapter_config.json", "adapter_model.safetensors")


class MissingAdapter(RuntimeError):
    pass


def package(out_dir: Path, dest: Path) -> Path:
    missing = [f for f in ADAPTER_FILES if not (out_dir / f).exists()]
    if missing:
        raise MissingAdapter(f"engine finished but did not write {missing} to {out_dir}")
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name in ADAPTER_FILES:
            path = out_dir / name
            os.chmod(path, 0o644)
            zf.write(path, arcname=name)
    return dest


def read_metrics(out_dir: Path) -> dict:
    """Final numbers from the engine's all_results.json (train_loss,
    eval_loss, train_runtime, ...). Empty if the engine wrote none."""
    path = out_dir / "all_results.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}
    return {k: v for k, v in data.items() if isinstance(v, (int, float))}
