from daemon.config import DaemonConfig
from daemon.executors.base import BaseExecutor
from daemon.executors.vllm import VLLMExecutor
from daemon.executors.ollama import OllamaExecutor

def create_executor(config: DaemonConfig) -> BaseExecutor:
    """Create the appropriate executor based on runtime config."""
    if config.runtime == "ollama":
        return OllamaExecutor(
            base_url=config.ollama_url,
            timeout=config.vllm_timeout,  # reuse timeout field
        )
    elif config.runtime == "vllm":
        return VLLMExecutor(
            base_url=config.vllm_url,
            timeout=config.vllm_timeout,
            supported_models=config.models if config.models else None,
        )
    else:
        raise ValueError(f"Unknown runtime: {config.runtime}")
