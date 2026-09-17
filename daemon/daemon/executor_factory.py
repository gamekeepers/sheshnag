import logging
from typing import Dict

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


def create_executors(config: DaemonConfig) -> Dict[str, BaseExecutor]:
    """Create one executor per configured runtime, keyed by runtime name."""
    executors: Dict[str, BaseExecutor] = {}
    for runtime in config.runtime:
        if runtime == "vllm" and gpu_vendors_present() == ["amd"]:
            # Runtime guard (issue #52): say it at startup, not at first prompt.
            logger.warning(f"runtime=vllm on an AMD-only machine. {VLLM_ROCM_HINT}")
        if runtime == "ollama":
            executors[runtime] = OllamaExecutor(
                base_url=config.ollama_url,
                timeout=config.inference_timeout,
                max_concurrent=config.max_concurrent_prompts,
                models_dir=config.ollama_models_dir,
            )
        elif runtime == "vllm":
            # With multiple runtimes the flat config.models list is ambiguous
            # (a name may be served by the other runtime), so skip the
            # "expected model served" health phase — inventory tells us what
            # vLLM actually has.
            supported = (
                config.models
                if config.models and len(config.runtime) == 1
                else None
            )
            executors[runtime] = VLLMExecutor(
                base_url=config.vllm_url,
                hf_hub_cache=config.hf_hub_cache,
                timeout=config.inference_timeout,
                supported_models=supported or None,
                max_concurrent=config.max_concurrent_prompts,
            )
        else:
            raise ValueError(f"Unknown runtime: {runtime}")
    return executors
