"""Tests for OpenAI parameter compatibility (issue #39).

Covers the translation layer (Ollama options nesting), warn-and-drop
policy, n>1 rejection, stream=true rejection, and vLLM unsupported
top-level warnings.

All tests use mocked HTTP transport consistent with the existing
patterns in test_ollama.py.  Live verification against a running
Ollama or vLLM server is pending — see docs/openai_compatibility.md.
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import logging

import httpx

from daemon.executors.ollama import OllamaExecutor
from daemon.executors.vllm import VLLMExecutor
from daemon.models import CompletionResult, PromptRequest, Job


# ── Helpers ─────────────────────────────────────────────────────

def create_mock_response(status_code: int, json_data: dict) -> httpx.Response:
    req = httpx.Request("POST", "http://localhost:11434/api/chat")
    return httpx.Response(status_code=status_code, json=json_data, request=req)


OLLAMA_CHAT_OK = {
    "message": {"role": "assistant", "content": "Hello!"},
    "done": True,
    "model": "llama3:8b",
    "prompt_eval_count": 10,
    "eval_count": 5,
}


# ════════════════════════════════════════════════════════════════
#  Deliverable 2 — Ollama translation layer
# ════════════════════════════════════════════════════════════════


class TestOllamaOptionTranslation:
    """Every OpenAI sampling parameter must land in options.*."""

    def test_top_p_nested(self):
        executor = OllamaExecutor()
        body = {"model": "m", "messages": [], "top_p": 0.9}
        translated = executor._translate_request(body)
        assert translated["options"]["top_p"] == 0.9
        assert "top_p" not in translated  # not top-level

    def test_top_k_nested(self):
        executor = OllamaExecutor()
        body = {"model": "m", "messages": [], "top_k": 40}
        translated = executor._translate_request(body)
        assert translated["options"]["top_k"] == 40

    def test_stop_nested(self):
        executor = OllamaExecutor()
        body = {"model": "m", "messages": [], "stop": ["\n", "user:"]}
        translated = executor._translate_request(body)
        assert translated["options"]["stop"] == ["\n", "user:"]

    def test_seed_nested(self):
        executor = OllamaExecutor()
        body = {"model": "m", "messages": [], "seed": 42}
        translated = executor._translate_request(body)
        assert translated["options"]["seed"] == 42

    def test_frequency_penalty_nested(self):
        executor = OllamaExecutor()
        body = {"model": "m", "messages": [], "frequency_penalty": 0.5}
        translated = executor._translate_request(body)
        assert translated["options"]["frequency_penalty"] == 0.5

    def test_presence_penalty_nested(self):
        executor = OllamaExecutor()
        body = {"model": "m", "messages": [], "presence_penalty": 1.2}
        translated = executor._translate_request(body)
        assert translated["options"]["presence_penalty"] == 1.2

    def test_all_params_together(self):
        executor = OllamaExecutor()
        body = {
            "model": "m",
            "messages": [{"role": "user", "content": "hi"}],
            "temperature": 0.5,
            "max_tokens": 100,
            "top_p": 0.95,
            "top_k": 50,
            "stop": ["END"],
            "seed": 123,
            "frequency_penalty": 0.3,
            "presence_penalty": 0.8,
        }
        translated = executor._translate_request(body)
        opts = translated["options"]
        assert opts["temperature"] == 0.5
        assert opts["num_predict"] == 100
        assert opts["top_p"] == 0.95
        assert opts["top_k"] == 50
        assert opts["stop"] == ["END"]
        assert opts["seed"] == 123
        assert opts["frequency_penalty"] == 0.3
        assert opts["presence_penalty"] == 0.8

    def test_defaults_unchanged_when_params_absent(self):
        """Params not in the body should not appear in options."""
        executor = OllamaExecutor()
        body = {"model": "m", "messages": []}
        translated = executor._translate_request(body)
        opts = translated["options"]
        assert "top_p" not in opts
        assert "top_k" not in opts
        assert "stop" not in opts
        assert "seed" not in opts
        assert "frequency_penalty" not in opts
        assert "presence_penalty" not in opts
        # temperature and num_predict always have defaults
        assert "temperature" in opts
        assert "num_predict" in opts


class TestOllamaToolsPassthrough:
    """tools must be top-level in the Ollama body, not nested in options."""

    def test_tools_at_top_level(self):
        executor = OllamaExecutor()
        tools = [{"type": "function", "function": {"name": "get_weather"}}]
        body = {"model": "m", "messages": [], "tools": tools}
        translated = executor._translate_request(body)
        assert translated["tools"] == tools
        assert "tools" not in translated.get("options", {})

    def test_no_tools_key_when_absent(self):
        executor = OllamaExecutor()
        body = {"model": "m", "messages": []}
        translated = executor._translate_request(body)
        assert "tools" not in translated


# ════════════════════════════════════════════════════════════════
#  Deliverable 3 — Warn-and-drop (no silent drops)
# ════════════════════════════════════════════════════════════════


class TestOllamaWarnAndDrop:
    """Unsupported params are logged, not silently dropped."""

    def test_logprobs_warned(self, caplog):
        executor = OllamaExecutor()
        body = {"model": "m", "messages": [], "logprobs": True}
        with caplog.at_level(logging.WARNING):
            translated = executor._translate_request(body)
        assert "logprobs" not in translated
        assert "logprobs" not in translated.get("options", {})
        assert any("logprobs" in r.message and "dropped" in r.message
                    for r in caplog.records)

    def test_top_logprobs_warned(self, caplog):
        executor = OllamaExecutor()
        body = {"model": "m", "messages": [], "top_logprobs": 5}
        with caplog.at_level(logging.WARNING):
            translated = executor._translate_request(body)
        assert "top_logprobs" not in translated
        assert "top_logprobs" not in translated.get("options", {})
        assert any("top_logprobs" in r.message and "dropped" in r.message
                    for r in caplog.records)

    def test_tool_choice_warned(self, caplog):
        """tool_choice must NOT be dropped silently — issue #39 gap 1."""
        executor = OllamaExecutor()
        body = {"model": "m", "messages": [], "tool_choice": "auto"}
        with caplog.at_level(logging.WARNING):
            translated = executor._translate_request(body)
        assert "tool_choice" not in translated
        assert "tool_choice" not in translated.get("options", {})
        assert any("tool_choice" in r.message and "dropped" in r.message
                    for r in caplog.records)

    def test_multiple_unsupported_all_warned(self, caplog):
        executor = OllamaExecutor()
        body = {
            "model": "m", "messages": [],
            "logprobs": True,
            "top_logprobs": 3,
            "tool_choice": "required",
        }
        with caplog.at_level(logging.WARNING):
            executor._translate_request(body)
        warned_params = [r.message for r in caplog.records
                         if "dropped" in r.message]
        assert len(warned_params) == 3


# ════════════════════════════════════════════════════════════════
#  n > 1 rejection
# ════════════════════════════════════════════════════════════════


class TestOllamaNRejection:

    def test_n_greater_than_1_raises(self):
        executor = OllamaExecutor()
        body = {"model": "m", "messages": [], "n": 3}
        with pytest.raises(ValueError, match="n=3"):
            executor._translate_request(body)

    def test_n_equals_1_accepted(self):
        executor = OllamaExecutor()
        body = {"model": "m", "messages": [], "n": 1}
        translated = executor._translate_request(body)
        # n=1 should not raise and should not appear in output
        assert "n" not in translated
        assert "n" not in translated.get("options", {})

    def test_n_absent_accepted(self):
        executor = OllamaExecutor()
        body = {"model": "m", "messages": []}
        translated = executor._translate_request(body)
        assert "n" not in translated

    @pytest.mark.asyncio
    async def test_n_rejection_caught_in_execute(self):
        """Gap 4: ValueError from n>1 must be caught by execute(),
        not propagate uncaught."""
        executor = OllamaExecutor()
        executor.version = "0.5.1"
        prompt = PromptRequest(
            custom_id="req-n3",
            body={"model": "m", "messages": [], "n": 3},
        )
        result = await executor.execute(prompt)
        assert not result.is_success
        assert "n=3" in result.error
        assert "not supported" in result.error


# ════════════════════════════════════════════════════════════════
#  Deliverable 4 — stream: true rejection (Worker level)
# ════════════════════════════════════════════════════════════════


class TestStreamRejection:

    @pytest.mark.asyncio
    async def test_stream_true_rejected_before_executor(self):
        """stream=true prompts must be rejected before reaching the
        executor, producing a CompletionResult with UNSUPPORTED_PARAMETER."""
        from daemon.worker import Worker
        from daemon.config import DaemonConfig
        from daemon.client import BackendClient

        config = DaemonConfig(worker_id="test-worker")
        client = AsyncMock(spec=BackendClient)
        executor = AsyncMock(spec=OllamaExecutor)

        worker = Worker(config=config, client=client, executor=executor)
        worker._running = True

        job = Job(job_id="test-job", model="m")
        prompts = [
            PromptRequest(
                custom_id="req-stream",
                body={"model": "m", "messages": [], "stream": True},
            ),
        ]

        results = await worker._run_prompts(prompts, job)

        assert len(results) == 1
        assert not results[0].is_success
        assert "UNSUPPORTED_PARAMETER" in results[0].error
        assert "stream=true" in results[0].error
        # Executor should NOT have been called
        executor.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_stream_false_reaches_executor(self):
        """stream=false should not be rejected."""
        from daemon.worker import Worker
        from daemon.config import DaemonConfig
        from daemon.client import BackendClient

        config = DaemonConfig(worker_id="test-worker")
        client = AsyncMock(spec=BackendClient)
        executor = AsyncMock(spec=OllamaExecutor)
        executor.execute.return_value = CompletionResult(
            custom_id="req-ok",
            response={"choices": [{"message": {"content": "hi"}}]},
        )

        worker = Worker(config=config, client=client, executor=executor)
        worker._running = True

        job = Job(job_id="test-job", model="m")
        prompts = [
            PromptRequest(
                custom_id="req-ok",
                body={"model": "m", "messages": [], "stream": False},
            ),
        ]

        results = await worker._run_prompts(prompts, job)

        assert len(results) == 1
        assert results[0].is_success
        executor.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_stream_absent_reaches_executor(self):
        """No stream key at all should not be rejected."""
        from daemon.worker import Worker
        from daemon.config import DaemonConfig
        from daemon.client import BackendClient

        config = DaemonConfig(worker_id="test-worker")
        client = AsyncMock(spec=BackendClient)
        executor = AsyncMock(spec=OllamaExecutor)
        executor.execute.return_value = CompletionResult(
            custom_id="req-no-stream",
            response={"choices": [{"message": {"content": "hi"}}]},
        )

        worker = Worker(config=config, client=client, executor=executor)
        worker._running = True

        job = Job(job_id="test-job", model="m")
        prompts = [
            PromptRequest(
                custom_id="req-no-stream",
                body={"model": "m", "messages": []},
            ),
        ]

        results = await worker._run_prompts(prompts, job)

        assert len(results) == 1
        assert results[0].is_success
        executor.execute.assert_called_once()


# ════════════════════════════════════════════════════════════════
#  vLLM warn-and-drop
# ════════════════════════════════════════════════════════════════


class TestVLLMWarnAndDrop:

    @pytest.mark.asyncio
    async def test_top_k_warned(self, caplog):
        """vLLM top_k: warn-and-drop since it requires extra_body."""
        executor = VLLMExecutor(base_url="http://localhost:8100")

        prompt = PromptRequest(
            custom_id="req-topk",
            body={"model": "m", "messages": [], "top_k": 50},
        )

        mock_response = httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "hi"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
            },
            request=httpx.Request("POST", "http://localhost:8100/v1/chat/completions"),
        )

        with caplog.at_level(logging.WARNING):
            with patch.object(httpx.AsyncClient, "request",
                              new_callable=AsyncMock) as mock_req:
                mock_req.return_value = mock_response
                result = await executor.execute(prompt)

        assert result.is_success
        assert any("top_k" in r.message and "dropped" in r.message
                    for r in caplog.records)


# ════════════════════════════════════════════════════════════════
#  End-to-end: Ollama execute with translated params
# ════════════════════════════════════════════════════════════════


class TestOllamaExecuteWithParams:

    @pytest.mark.asyncio
    async def test_translated_params_reach_ollama(self):
        """Verify that sampling params are correctly nested in the
        POST /api/chat body sent to Ollama."""
        executor = OllamaExecutor()
        executor.version = "0.5.1"

        prompt = PromptRequest(
            custom_id="req-full",
            body={
                "model": "llama3:8b",
                "messages": [{"role": "user", "content": "hi"}],
                "temperature": 0.3,
                "max_tokens": 200,
                "top_p": 0.8,
                "seed": 99,
                "stop": ["END"],
                "frequency_penalty": 0.5,
                "presence_penalty": 0.2,
            },
        )

        mock_response = create_mock_response(200, OLLAMA_CHAT_OK)

        with patch.object(httpx.AsyncClient, "post",
                          new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response
            result = await executor.execute(prompt)

        assert result.is_success

        # Verify what was sent to Ollama
        call_args = mock_post.call_args
        sent_body = call_args.kwargs.get("json") or call_args[1].get("json")
        assert sent_body["options"]["temperature"] == 0.3
        assert sent_body["options"]["num_predict"] == 200
        assert sent_body["options"]["top_p"] == 0.8
        assert sent_body["options"]["seed"] == 99
        assert sent_body["options"]["stop"] == ["END"]
        assert sent_body["options"]["frequency_penalty"] == 0.5
        assert sent_body["options"]["presence_penalty"] == 0.2
        assert sent_body["stream"] is False

    @pytest.mark.asyncio
    async def test_tools_not_nested_in_options(self):
        """tools must be top-level in the Ollama body, NOT in options."""
        executor = OllamaExecutor()
        executor.version = "0.5.1"

        tools = [{"type": "function", "function": {"name": "f"}}]
        prompt = PromptRequest(
            custom_id="req-tools",
            body={
                "model": "llama3:8b",
                "messages": [{"role": "user", "content": "hi"}],
                "tools": tools,
            },
        )

        mock_response = create_mock_response(200, OLLAMA_CHAT_OK)

        with patch.object(httpx.AsyncClient, "post",
                          new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response
            await executor.execute(prompt)

        sent_body = mock_post.call_args.kwargs.get("json") or \
                    mock_post.call_args[1].get("json")
        assert sent_body["tools"] == tools
        assert "tools" not in sent_body.get("options", {})
