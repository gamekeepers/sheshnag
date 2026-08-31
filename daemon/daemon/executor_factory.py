import logging

from daemon.config import DaemonConfig
from daemon.executors.base import BaseExecutor
from daemon.executors.vllm import VLLMExecutor
from daemon.executors.ollama import OllamaExecutor
from daemon.hardware import gpu_vendors_present

logger = logging.getLogger(__name__)

VLLM_ROCM_HINT = (
    "vLLM on AMD GPUs needs a ROCm build of vLLM — the default PyPI wheels "
    "are CUDA-only and will not see the GPU. Build from source with ROCm or "
    "use the rocm/vllm container, then start `vllm serve` before the daemon. "
    "Ollama supports ROCm out of the box if you would rather switch runtime."
)


def create_executor(config: DaemonConfig) -> BaseExecutor:
    """Create the appropriate executor based on runtime config."""
    if config.runtime == "vllm" and gpu_vendors_present() == ["amd"]:
        # Runtime guard (issue #52): say it at startup, not at first prompt.
        logger.warning(f"runtime=vllm on an AMD-only machine. {VLLM_ROCM_HINT}")
    if config.runtime == "ollama":
        return OllamaExecutor(
            base_url=config.ollama_url,
            timeout=config.inference_timeout,
        )
    elif config.runtime == "vllm":
        return VLLMExecutor(
            base_url=config.vllm_url,
            timeout=config.inference_timeout,
            supported_models=config.models if config.models else None,
        )
    else:
        raise ValueError(f"Unknown runtime: {config.runtime}")
