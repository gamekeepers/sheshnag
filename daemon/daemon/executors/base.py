"""
Abstract base class for inference executors.

This is the core abstraction that enables the Open/Closed Principle:
    - The Worker class is CLOSED for modification (never changes when
      adding new runtimes).
    - The executor system is OPEN for extension (subclass BaseExecutor
      to support Ollama, TGI, llama.cpp, etc.).

Any class that implements `execute()` and `health_check()` can be
plugged into the Worker via dependency injection.

Week 2+ extensions:
    - `batch_execute()` for runtimes that support native batching
    - `get_model_info()` for capability discovery
    - `close()` for cleanup
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

from daemon.models import CompletionResult, PromptRequest


class BaseExecutor(ABC):
    """
    Contract for all inference runtime executors.

    Subclasses MUST implement:
        - execute(): process a single prompt
        - health_check(): verify the runtime is reachable

    Subclasses MAY override:
        - batch_execute(): optimize for batch processing
        - close(): release resources on shutdown
    """

    @abstractmethod
    async def execute(self, prompt: PromptRequest) -> CompletionResult:
        """
        Execute a single inference prompt.

        This method should be idempotent — calling it again with the
        same prompt should produce a valid (if potentially different)
        result. This is important for retry logic in future weeks.

        Args:
            prompt: Parsed JSONL row with the request body.

        Returns:
            CompletionResult with either a response or an error.
            This method should NEVER raise — errors go in the result.
        """
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """
        Verify the inference backend is reachable and healthy.

        Returns:
            True if the backend is ready to accept requests.
        """
        ...

    async def query_concurrency_limit(self) -> Optional[int]:
        """
        Query the runtime for its max parallel request capacity.
        Returns None if unsupported or query fails.
        """
        return None

    async def batch_execute(
        self, prompts: List[PromptRequest]
    ) -> List[CompletionResult]:
        """
        Execute a batch of prompts.

        Default implementation processes sequentially.
        Override for runtimes that support native batch APIs.

        Args:
            prompts: List of parsed JSONL rows.

        Returns:
            List of CompletionResult in the same order as input.
        """
        results = []
        for prompt in prompts:
            result = await self.execute(prompt)
            results.append(result)
        return results

    async def close(self) -> None:
        """
        Release any resources held by the executor.

        Override if the executor maintains persistent connections,
        file handles, or subprocess references.
        """
        pass
