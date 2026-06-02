"""
Executor package — runtime backends for inference execution.

The executor layer implements the Strategy pattern:
    - BaseExecutor defines the interface
    - Concrete executors (VLLMExecutor) implement it
    - Worker depends on the abstraction, not the implementation

To add a new runtime (e.g., Ollama, TGI), create a new subclass
of BaseExecutor in this package. No changes needed in Worker.
"""

from daemon.executors.base import BaseExecutor
from daemon.executors.vllm import VLLMExecutor

__all__ = ["BaseExecutor", "VLLMExecutor"]
