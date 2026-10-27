"""Engine output -> a failure a user can act on.

The runner's job is to turn what LLaMA-Factory prints into a message that
says what happened and what to do, and to decide whether a retry could
help (a dataset or config problem fails the same way every time).
"""
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class EngineFailure:
    code: str
    message: str
    retryable: bool


# First match wins; ordered most specific first.
_RULES = [
    (r"CUDA out of memory|OutOfMemoryError",
     "gpu_out_of_memory", True,
     "The GPU ran out of memory during training. This worker's GPU is shared and had too "
     "little free VRAM for this job; lower hyperparameters.max_seq_length or lora.rank, "
     "or resubmit when the GPU is less busy."),
    (r"Some keys are not used by the HfArgumentParser",
     "runner_config_rejected", False,
     "The training engine rejected the configuration forge-runner generated. This is a "
     "platform bug, not a problem with your job; it has been logged."),
    (r"Cannot find valid samples|No valid samples|num_samples=0",
     "no_trainable_examples", False,
     "No training examples were left after applying the model's chat template. Check that "
     "every example ends with an assistant turn and fits within max_seq_length."),
    (r"Revision Not Found|RevisionNotFoundError|Invalid rev",
     "base_revision_missing", False,
     "The pinned revision of the base model no longer exists on Hugging Face. A platform "
     "admin must re-pin this catalogue entry."),
    (r"GatedRepoError|Repository Not Found|401 Client Error|403 Client Error",
     "base_model_unavailable", False,
     "The base model could not be downloaded from Hugging Face (repository missing or "
     "access-gated). A platform admin must fix the catalogue entry."),
    (r"ConnectionError|Max retries exceeded|Temporary failure in name resolution|ReadTimeout",
     "network_error", True,
     "The worker lost its network connection while downloading the base model. Retrying."),
    (r"No space left on device",
     "worker_disk_full", True,
     "The worker ran out of disk space. Retrying on a worker with free space."),
    (r"Cannot connect to the Docker daemon|docker: Error response from daemon",
     "engine_not_started", True,
     "The training engine container could not be started on this worker."),
]

_ERROR_LINE = re.compile(r"(Error|Exception|error:)")


def _last_error_line(log: str) -> str:
    lines = [l.strip() for l in log.splitlines() if l.strip()]
    for line in reversed(lines):
        if _ERROR_LINE.search(line):
            return line[:300]
    return lines[-1][:300] if lines else "(no output)"


def classify(log: str, returncode: int) -> EngineFailure:
    for pattern, code, retryable, message in _RULES:
        if re.search(pattern, log):
            return EngineFailure(code, message, retryable)
    if returncode in (137, -9):
        return EngineFailure(
            "engine_killed",
            "The training process was killed (exit 137), usually because it hit the "
            "container's memory limit.",
            True)
    return EngineFailure(
        "training_failed",
        f"The training engine exited with code {returncode}. Last error: {_last_error_line(log)}",
        True)
