"""Read LLaMA-Factory's trainer_log.jsonl as it grows.

G2 picked LLaMA-Factory partly for this file: one JSON object per log step
(current_steps, total_steps, loss, lr, epoch, ...), so a supervisor can
follow training without scraping human log lines.
"""
import json
from pathlib import Path
from typing import Optional


class TrainerLogTail:
    """Incremental reader. A line still being written is held back until its
    newline arrives, so a half-written object is never parsed."""

    def __init__(self, path: Path):
        self.path = path
        self._offset = 0
        self._partial = ""

    def read_new(self) -> list[dict]:
        if not self.path.exists():
            return []
        with open(self.path, "r", encoding="utf-8") as fh:
            fh.seek(self._offset)
            chunk = fh.read()
            self._offset = fh.tell()
        lines = (self._partial + chunk).split("\n")
        self._partial = lines.pop()
        entries = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict):
                entries.append(entry)
        return entries


def to_progress(entry: dict) -> Optional[dict]:
    """trainer_log.jsonl entry -> /workers/finetune-progress fields, or None
    for entries without a step position."""
    if "current_steps" not in entry or "total_steps" not in entry:
        return None
    progress = {
        "current_step": int(entry["current_steps"]),
        "total_steps": int(entry["total_steps"]),
        "current_epoch": float(entry.get("epoch") or 0.0),
    }
    for src, dst in (("loss", "loss"), ("lr", "learning_rate"), ("eval_loss", "eval_loss")):
        if isinstance(entry.get(src), (int, float)):
            progress[dst] = float(entry[src])
    return progress
