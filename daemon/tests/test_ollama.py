import pytest
from unittest.mock import AsyncMock, patch
import httpx
import jsonschema

from daemon.executors.ollama import OllamaExecutor, parse_version
from daemon.models import PromptRequest, CompletionResult

# Helper to create responses with mock request bound to prevent raise_for_status issues
def create_mock_response(status_code: int, json_data: dict) -> httpx.Response:
    req = httpx.Request("POST", "http://localhost:11434/api/chat")
    return httpx.Response(status_code=status_code, json=json_data, request=req)

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
async def test_health_check_success():
    executor = OllamaExecutor()
    
    version_res = create_mock_response(200, {"version": "0.5.1"})
    tags_res = create_mock_response(200, {"models": []})
    
    with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = [version_res, tags_res]
        
        healthy = await executor.health_check()
        assert healthy is True
        assert executor.version == "0.5.1"


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


