import pytest
from unittest.mock import AsyncMock, patch
import httpx
import jsonschema

from daemon.executors.ollama import (
    OllamaExecutor,
    parse_version,
    VERSION_PROBE_RETRY_SECONDS,
)
from daemon.models import PromptRequest, CompletionResult

# Helper to create responses with mock request bound to prevent raise_for_status issues
def create_mock_response(status_code: int, json_data: dict, headers: dict = None) -> httpx.Response:
    req = httpx.Request("POST", "http://localhost:11434/api/chat")
    return httpx.Response(status_code=status_code, json=json_data, headers=headers, request=req)

# Test parse_version helper
def test_parse_version():
    assert parse_version("0.5.1") == (0, 5, 1)
    assert parse_version("v0.5.0") == (0, 5, 0)
    assert parse_version("0.5.0-rc1") == (0, 5, 0)
    assert parse_version("0.10.2+cuda") == (0, 10, 2)
    assert parse_version("1.0") == (1, 0, 0)
    assert parse_version("") == (0, 0, 0)
    assert parse_version(None) == (0, 0, 0)
    assert parse_version("invalid") == (0, 0, 0)


# Test request translation
def test_translate_request_loose_mode():
    executor = OllamaExecutor()
    openai_body = {
        "model": "llama3:8b",
        "messages": [{"role": "user", "content": "hello"}],
        "response_format": {"type": "json_object"}
    }
    translated = executor._translate_request(openai_body)
    assert translated["format"] == "json"
    assert translated["model"] == "llama3:8b"


def test_translate_request_strict_mode():
    executor = OllamaExecutor()
    schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"]
    }
    openai_body = {
        "model": "llama3:8b",
        "messages": [{"role": "user", "content": "hello"}],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"schema": schema}
        }
    }
    translated = executor._translate_request(openai_body)
    assert translated["format"] == schema


def test_translate_request_regression():
    executor = OllamaExecutor()
    openai_body = {
        "model": "llama3:8b",
        "messages": [{"role": "user", "content": "hello"}]
    }
    translated = executor._translate_request(openai_body)
    assert "format" not in translated


@pytest.mark.asyncio
async def test_execute_version_gate_fails():
    # If version < 0.5.0, execution must fail early without hitting Ollama
    executor = OllamaExecutor()
    executor.version = "0.4.8"
    
    prompt = PromptRequest(
        custom_id="req-1",
        body={
            "model": "llama3:8b",
            "messages": [{"role": "user", "content": "hello"}],
            "response_format": {"type": "json_object"}
        }
    )
    
    res = await executor.execute(prompt)
    assert not res.is_success
    assert "VERSION_MISMATCH" in res.error


@pytest.mark.asyncio
async def test_execute_version_gate_succeeds_version_0_5_0():
    executor = OllamaExecutor()
    executor.version = "0.5.0"
    
    prompt = PromptRequest(
        custom_id="req-1",
        body={
            "model": "llama3:8b",
            "messages": [{"role": "user", "content": "hello"}],
            "response_format": {"type": "json_object"}
        }
    )
    
    mock_response = create_mock_response(
        200,
        {
            "message": {"role": "assistant", "content": '{"status": "ok"}'},
            "done": True,
            "model": "llama3:8b"
        }
    )
    
    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        res = await executor.execute(prompt)
        assert res.is_success
        assert res.response["choices"][0]["message"]["content"] == '{"status": "ok"}'


@pytest.mark.asyncio
async def test_execute_loose_mode_valid_json():
    executor = OllamaExecutor()
    executor.version = "0.5.1"
    
    prompt = PromptRequest(
        custom_id="req-1",
        body={
            "model": "llama3:8b",
            "messages": [{"role": "user", "content": "hello"}],
            "response_format": {"type": "json_object"}
        }
    )
    
    mock_response = create_mock_response(
        200,
        {
            "message": {"role": "assistant", "content": '{"key": "value"}'},
            "done": True,
            "model": "llama3:8b"
        }
    )
    
    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        res = await executor.execute(prompt)
        assert res.is_success
        assert res.error is None


@pytest.mark.asyncio
async def test_execute_loose_mode_invalid_json():
    executor = OllamaExecutor()
    executor.version = "0.5.1"
    
    prompt = PromptRequest(
        custom_id="req-1",
        body={
            "model": "llama3:8b",
            "messages": [{"role": "user", "content": "hello"}],
            "response_format": {"type": "json_object"}
        }
    )
    
    # Model returned prose instead of JSON
    mock_response = create_mock_response(
        200,
        {
            "message": {"role": "assistant", "content": 'Sure, here is the result: {key: value}'},
            "done": True,
            "model": "llama3:8b",
            "prompt_eval_count": 10,
            "eval_count": 20
        }
    )
    
    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        res = await executor.execute(prompt)
        assert not res.is_success
        assert "JSON_PARSE_ERROR" in res.error
        assert res.response is not None
        assert res.response["choices"][0]["message"]["content"] == 'Sure, here is the result: {key: value}'
        assert res.usage == {
            "prompt_tokens": 10,
            "completion_tokens": 20,
            "total_tokens": 30
        }


@pytest.mark.asyncio
async def test_execute_strict_mode_valid_schema():
    executor = OllamaExecutor()
    executor.version = "0.5.1"
    
    schema = {
        "type": "object",
        "properties": {"age": {"type": "integer"}},
        "required": ["age"]
    }
    prompt = PromptRequest(
        custom_id="req-1",
        body={
            "model": "llama3:8b",
            "messages": [{"role": "user", "content": "hello"}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"schema": schema}
            }
        }
    )
    
    mock_response = create_mock_response(
        200,
        {
            "message": {"role": "assistant", "content": '{"age": 25}'},
            "done": True,
            "model": "llama3:8b"
        }
    )
    
    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        res = await executor.execute(prompt)
        assert res.is_success
        assert res.error is None


@pytest.mark.asyncio
async def test_execute_strict_mode_schema_violation():
    executor = OllamaExecutor()
    executor.version = "0.5.1"
    
    schema = {
        "type": "object",
        "properties": {"age": {"type": "integer"}},
        "required": ["age"]
    }
    prompt = PromptRequest(
        custom_id="req-1",
        body={
            "model": "llama3:8b",
            "messages": [{"role": "user", "content": "hello"}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"schema": schema}
            }
        }
    )
    
    # Model returned valid JSON, but age is a string instead of an integer
    mock_response = create_mock_response(
        200,
        {
            "message": {"role": "assistant", "content": '{"age": "twenty-five"}'},
            "done": True,
            "model": "llama3:8b",
            "prompt_eval_count": 15,
            "eval_count": 25
        }
    )
    
    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        res = await executor.execute(prompt)
        assert not res.is_success
        assert "SCHEMA_VIOLATION" in res.error
        assert res.response is not None
        assert res.response["choices"][0]["message"]["content"] == '{"age": "twenty-five"}'
        assert res.usage == {
            "prompt_tokens": 15,
            "completion_tokens": 25,
            "total_tokens": 40
        }


@pytest.mark.asyncio
async def test_execute_empty_response_choices():
    executor = OllamaExecutor()
    executor.version = "0.5.1"

    prompt = PromptRequest(
        custom_id="req-empty-choices",
        body={
            "model": "llama3:8b",
            "messages": [{"role": "user", "content": "hello"}],
            "response_format": {"type": "json_object"}
        }
    )

    # Response with no choices / empty message
    mock_response = create_mock_response(
        200,
        {
            "done": True,
            "model": "llama3:8b",
        }
    )

    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        res = await executor.execute(prompt)
        assert not res.is_success
        assert res.error.startswith("EMPTY_RESPONSE:")
        assert "Response contains no choices" in res.error
        assert res.response is not None


@pytest.mark.asyncio
async def test_execute_empty_response_choices_non_structured():
    executor = OllamaExecutor()
    executor.version = "0.5.1"

    prompt = PromptRequest(
        custom_id="req-empty-choices-plain",
        body={
            "model": "llama3:8b",
            "messages": [{"role": "user", "content": "hello"}],
        }
    )

    # Response with no choices / empty message
    mock_response = create_mock_response(
        200,
        {
            "done": True,
            "model": "llama3:8b",
        }
    )

    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        res = await executor.execute(prompt)
        assert not res.is_success
        assert res.error.startswith("EMPTY_RESPONSE:")
        assert "Response contains no choices" in res.error
        assert res.response is not None


@pytest.mark.asyncio
async def test_health_check_success(caplog):
    executor = OllamaExecutor()

    version_res = create_mock_response(200, {"version": "0.5.1"})
    tags_res = create_mock_response(200, {"models": []})

    with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = [version_res, tags_res]

        with caplog.at_level("INFO"):
            healthy = await executor.health_check()
        assert healthy is True
        assert executor.version == "0.5.1"
        # Real Ollama sends no Server header: the issue #80 warning must not fire
        assert "Detected non-Ollama" not in caplog.text


@pytest.mark.asyncio
async def test_health_check_version_failure():
    executor = OllamaExecutor()
    
    # Simulates a connection timeout or transient failure on the version check
    with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = Exception("Connection timed out")
        
        healthy = await executor.health_check()
        assert healthy is False
        assert executor.version is None


@pytest.mark.asyncio
async def test_execute_version_unreachable():
    executor = OllamaExecutor()
    executor.version = None
    
    prompt = PromptRequest(
        custom_id="req-unreachable",
        body={
            "model": "llama3:8b",
            "messages": [{"role": "user", "content": "hello"}],
            "response_format": {"type": "json_object"}
        }
    )
    
    # Mock health_check to return False (leaving version as None)
    with patch.object(executor, "health_check", new_callable=AsyncMock) as mock_health:
        mock_health.return_value = False
        res = await executor.execute(prompt)
        assert not res.is_success
        assert "OLLAMA_UNREACHABLE" in res.error


# ── Version probe caching (issue #83) ────────────────────────────────────────
#
# These drive the real health_check() against a mocked transport rather than
# patching health_check out, so the probe bookkeeping inside it is covered too.
# The clock is faked: the retry window is wall-clock, and a test that sleeps
# through 60s of it is not a test anyone will keep running.

def _probe_counting_executor(server_up):
    """An executor whose /api/version answers according to `server_up['up']`.

    Returns (executor, counts) where counts["version"] is the number of real
    probes that reached the transport.
    """
    counts = {"version": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            counts["version"] += 1
        if not server_up["up"]:
            raise httpx.ConnectError("connection refused")
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.6.0"})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": []})
        return httpx.Response(200, json={
            "message": {"role": "assistant", "content": '{"ok": true}'},
            "model": "llama3:8b",
            "done": True,
        })

    executor = OllamaExecutor()
    executor._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://localhost:11434",
    )
    return executor, counts


def _json_prompt(custom_id):
    return PromptRequest(
        custom_id=custom_id,
        body={
            "model": "llama3:8b",
            "messages": [{"role": "user", "content": "hello"}],
            "response_format": {"type": "json_object"},
        },
    )


@pytest.mark.asyncio
async def test_version_probe_not_repeated_for_every_prompt():
    """Issue #83: a down server used to cost a 5s probe on every prompt, so a
    10k-row batch spent ~14 hours failing. Within the retry window the failed
    probe is cached and prompts fail immediately."""
    server = {"up": False}
    executor, counts = _probe_counting_executor(server)

    # Clock frozen: nothing can trip the retry window.
    with patch("daemon.executors.ollama.time.monotonic", return_value=1000.0):
        for n in range(5):
            res = await executor.execute(_json_prompt(f"req-{n}"))
            assert not res.is_success
            assert "OLLAMA_UNREACHABLE" in res.error

    assert counts["version"] == 1, "one probe for five prompts, not five"
    await executor._client.aclose()


@pytest.mark.asyncio
async def test_failed_version_probe_is_retried_after_the_window():
    """The negative cache must not be permanent. The daemon is allowed to start
    before Ollama is up (Worker._wait_for_executor retries for 60s and then
    proceeds anyway), so a latched failure would leave structured outputs dead
    for the life of the process while plain chat kept working."""
    server = {"up": False}
    executor, counts = _probe_counting_executor(server)

    now = [1000.0]
    with patch("daemon.executors.ollama.time.monotonic", side_effect=lambda: now[0]):
        # Startup pre-flight against a server that is down.
        assert await executor.health_check() is False
        assert executor.version is None
        assert counts["version"] == 1

        # Still inside the window: cached, no new probe, still failing.
        now[0] += VERSION_PROBE_RETRY_SECONDS - 1
        assert not (await executor.execute(_json_prompt("during"))).is_success
        assert counts["version"] == 1

        # Ollama comes up; the window elapses.
        server["up"] = True
        now[0] += 2
        res = await executor.execute(_json_prompt("after"))

    assert counts["version"] == 2, "the probe must be retried once the window passes"
    assert res.is_success, "structured outputs must work again once Ollama is back"
    assert executor.version == "0.6.0"
    await executor._client.aclose()


@pytest.mark.asyncio
async def test_successful_version_probe_is_cached_permanently():
    """A known version never needs re-probing — the retry window applies only to
    failures, so a healthy server still pays exactly one probe per process."""
    server = {"up": True}
    executor, counts = _probe_counting_executor(server)

    now = [1000.0]
    with patch("daemon.executors.ollama.time.monotonic", side_effect=lambda: now[0]):
        assert (await executor.execute(_json_prompt("first"))).is_success
        assert counts["version"] == 1
        # Far beyond the retry window.
        now[0] += VERSION_PROBE_RETRY_SECONDS * 100
        assert (await executor.execute(_json_prompt("much-later"))).is_success

    assert counts["version"] == 1
    await executor._client.aclose()


def test_translate_embeddings_request():
    executor = OllamaExecutor()
    openai_body = {
        "model": "nomic-embed-text",
        "input": "hello world",
        "truncate": True
    }
    translated = executor._translate_embeddings_request(openai_body)
    assert translated["model"] == "nomic-embed-text"
    assert translated["input"] == "hello world"
    assert translated["truncate"] is True


def test_translate_embeddings_response():
    executor = OllamaExecutor()
    ollama_response = {
        "model": "nomic-embed-text",
        "embeddings": [[0.1, 0.2, 0.3]],
        "prompt_eval_count": 5
    }
    translated = executor._translate_embeddings_response(ollama_response)
    assert translated["object"] == "list"
    assert len(translated["data"]) == 1
    assert translated["data"][0]["object"] == "embedding"
    assert translated["data"][0]["embedding"] == [0.1, 0.2, 0.3]
    assert translated["data"][0]["index"] == 0
    assert translated["usage"] == {
        "prompt_tokens": 5,
        "total_tokens": 5
    }


@pytest.mark.asyncio
async def test_execute_embeddings_routing():
    executor = OllamaExecutor()
    # Ensure health check does not throw version gating issues (even if version is old)
    executor.version = "0.4.0"
    
    prompt = PromptRequest(
        custom_id="req-embed-1",
        url="/v1/embeddings",
        body={
            "model": "nomic-embed-text",
            "input": "test text"
        }
    )
    
    mock_response = create_mock_response(
        200,
        {
            "model": "nomic-embed-text",
            "embeddings": [[0.5, 0.6]],
            "prompt_eval_count": 10
        }
    )
    
    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        res = await executor.execute(prompt)
        assert res.is_success
        assert res.response["object"] == "list"
        assert res.response["data"][0]["embedding"] == [0.5, 0.6]
        assert res.usage == {
            "prompt_tokens": 10,
            "total_tokens": 10
        }
        # Verify it went to /api/embed and not /api/chat
        mock_post.assert_called_once_with("/api/embed", json={
            "model": "nomic-embed-text",
            "input": "test text"
        })




def test_translate_embeddings_response_legacy_singular():
    """Legacy /api/embeddings returns a flat `embedding` vector, not `embeddings`.

    We call /api/embed, but a proxy or older server on the Ollama port can
    answer in the legacy shape. Normalising beats silently returning nothing.
    """
    executor = OllamaExecutor()
    translated = executor._translate_embeddings_response({
        "model": "nomic-embed-text:latest",
        "embedding": [0.1, 0.2, 0.3],
        "prompt_eval_count": 4,
    })
    assert len(translated["data"]) == 1
    assert translated["data"][0]["embedding"] == [0.1, 0.2, 0.3]
    assert translated["data"][0]["index"] == 0
    assert translated["usage"]["total_tokens"] == 4


def test_translate_embeddings_response_empty():
    """No embeddings key at all -> empty data, not a crash."""
    executor = OllamaExecutor()
    translated = executor._translate_embeddings_response({"model": "x"})
    assert translated["data"] == []
    assert translated["object"] == "list"


def test_translate_embeddings_response_multiple_inputs():
    """Indices must track input order for custom_id correlation."""
    executor = OllamaExecutor()
    translated = executor._translate_embeddings_response({
        "model": "nomic-embed-text:latest",
        "embeddings": [[0.1], [0.2], [0.3]],
        "prompt_eval_count": 9,
    })
    assert [d["index"] for d in translated["data"]] == [0, 1, 2]
    assert [d["embedding"] for d in translated["data"]] == [[0.1], [0.2], [0.3]]


@pytest.mark.asyncio
async def test_embeddings_failure_carries_error_code():
    """Embedding failures use the same PREFIX: convention as every other path."""
    executor = OllamaExecutor()
    prompt = PromptRequest(
        custom_id="req-embed-fail",
        url="/v1/embeddings",
        body={"model": "nomic-embed-text:latest", "input": "x"},
    )
    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = create_mock_response(500, {"error": "boom"})
        res = await executor.execute(prompt)
    assert not res.is_success
    assert res.error.startswith("EMBEDDING_FAILED:")


@pytest.mark.asyncio
async def test_embeddings_path_skips_version_probe():
    """Structured-output version gating is irrelevant to embeddings.

    Probing /api/version per embedding row costs a 5s timeout each when the
    server is unreachable, since a failed probe leaves self.version None.
    """
    executor = OllamaExecutor()
    assert executor.version is None
    prompt = PromptRequest(
        custom_id="req-embed-noprobe",
        url="/v1/embeddings",
        body={"model": "nomic-embed-text:latest", "input": "x"},
    )
    with patch.object(OllamaExecutor, "health_check", new_callable=AsyncMock) as hc:
        with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = create_mock_response(
                200, {"model": "m", "embeddings": [[0.1]], "prompt_eval_count": 1}
            )
            res = await executor.execute(prompt)
    assert res.is_success
    hc.assert_not_called()


@pytest.mark.asyncio
async def test_health_check_non_ollama_server_header_warning(caplog):
    """Regression for issue #80: an aiohttp app squatting on :11434 passed health
    checks silently. Warn when the Server header names a known app server."""
    executor = OllamaExecutor()
    version_resp = create_mock_response(
        200, {"version": "0.1.0"}, headers={"server": "Python/3.12 aiohttp/3.13.5"}
    )
    tags_resp = create_mock_response(200, {"models": []})

    with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = [version_resp, tags_resp]
        with caplog.at_level("INFO"):
            healthy = await executor.health_check()

    assert healthy is True
    assert "Server header from http://localhost:11434: Python/3.12 aiohttp/3.13.5" in caplog.text
    assert "Detected non-Ollama application server" in caplog.text


@pytest.mark.asyncio
async def test_health_check_server_header_warning_logged_once(caplog):
    """Issue #80 follow-up: health_check() runs per startup retry and per prompt
    while version is unset, so the header INFO+WARNING pair must be latched."""
    executor = OllamaExecutor()
    responses = [
        create_mock_response(
            200, {"version": "0.1.0"}, headers={"server": "Python/3.12 aiohttp/3.13.5"}
        ),
        create_mock_response(200, {"models": []}),
    ] * 2

    with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = responses
        with caplog.at_level("INFO"):
            await executor.health_check()
            await executor.health_check()

    assert caplog.text.count("Detected non-Ollama application server") == 1
    assert caplog.text.count("Server header from") == 1


@pytest.mark.asyncio
async def test_health_check_app_server_with_proxy_like_name_warns(caplog):
    """Issue #80 hardening: Apache-Coyote (Tomcat's connector) is an app server,
    not a proxy — the old proxy allowlist's 'apache' substring wrongly absolved it."""
    executor = OllamaExecutor()
    version_resp = create_mock_response(
        200, {"version": "0.1.0"}, headers={"server": "Apache-Coyote/1.1"}
    )
    tags_resp = create_mock_response(200, {"models": []})

    with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = [version_resp, tags_resp]
        with caplog.at_level("INFO"):
            healthy = await executor.health_check()

    assert healthy is True
    assert "Detected non-Ollama application server" in caplog.text


@pytest.mark.asyncio
async def test_health_check_reverse_proxy_server_header_normal(caplog):
    """Ollama behind a front-end (nginx, openresty, an ALB, …) completes without a
    false-positive warning: unknown Server values are not treated as squatters."""
    executor = OllamaExecutor()
    version_resp = create_mock_response(
        200, {"version": "0.5.1"}, headers={"server": "nginx/1.24.0 (Ubuntu)"}
    )
    tags_resp = create_mock_response(200, {"models": []})

    with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = [version_resp, tags_resp]
        with caplog.at_level("INFO"):
            healthy = await executor.health_check()

    assert healthy is True
    assert "Server header from http://localhost:11434: nginx/1.24.0 (Ubuntu)" in caplog.text
    assert "Detected non-Ollama" not in caplog.text


@pytest.mark.asyncio
async def test_health_check_non_200_server_header_logged(caplog):
    """Issue #80: a squatter returning 404/502 on /api/version must still get its
    Server header logged (before raise_for_status), with a status-aware message."""
    executor = OllamaExecutor()
    version_resp = create_mock_response(
        404, {"error": "not found"}, headers={"server": "uvicorn"}
    )

    with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = [version_resp]
        with caplog.at_level("INFO"):
            healthy = await executor.health_check()

    assert healthy is False
    assert "Server header from http://localhost:11434: uvicorn" in caplog.text
    assert "Detected non-Ollama application server" in caplog.text
    # On an error status the warning must not claim metadata endpoints responded
    assert "/api/version returned HTTP 404." in caplog.text
    assert "metadata endpoints respond" not in caplog.text

