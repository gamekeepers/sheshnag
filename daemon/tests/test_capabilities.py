"""Executor capabilities, as the control plane routes on them.

A capability the daemon claims and cannot honour turns into a per-row
failure hours later; one it fails to claim keeps a whole batch queued. So
each executor's answer is pinned here, including the version gate.
"""

from daemon.executors.base import BaseExecutor
from daemon.executors.llamacpp import LlamaCppExecutor
from daemon.executors.ollama import OllamaExecutor
from daemon.executors.vllm import VLLMExecutor

KEYS = {"logprobs", "completions", "prompt_scoring"}


class _Bare(BaseExecutor):
    async def execute(self, prompt):  # pragma: no cover - never called
        raise NotImplementedError

    async def health_check(self):  # pragma: no cover - never called
        return True


def test_base_default_claims_nothing():
    caps = _Bare().capabilities()
    assert set(caps) == KEYS
    assert not any(caps.values())


def test_ollama_logprobs_is_version_gated():
    executor = OllamaExecutor()
    assert executor.capabilities()["logprobs"] is False          # version unknown
    executor.version = "0.12.10"
    assert executor.capabilities()["logprobs"] is False
    executor.version = "0.12.11"
    assert executor.capabilities()["logprobs"] is True
    executor.version = "v0.32.14"
    assert executor.capabilities()["logprobs"] is True
    assert executor.capabilities()["completions"] is False
    assert executor.capabilities()["prompt_scoring"] is False


def test_vllm_claims_all_three():
    executor = VLLMExecutor(base_url="http://127.0.0.1:1")
    assert executor.capabilities() == {"logprobs": True, "completions": True, "prompt_scoring": True}


def test_llamacpp_has_no_echo():
    caps = LlamaCppExecutor(base_url="http://127.0.0.1:1").capabilities()
    assert caps == {"logprobs": True, "completions": True, "prompt_scoring": False}
