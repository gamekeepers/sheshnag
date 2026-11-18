"""forge_runner.dataset: OpenAI chat/tool-call JSONL -> LLaMA-Factory sharegpt rows.

The rendered result was also checked against LLaMA-Factory 0.9.5's own
SharegptDatasetConverter and qwen formatters (#194): the converted example
below renders as Qwen2.5's native <tool_call> / <tool_response> text.
"""
import json
import sys
from pathlib import Path

import pytest

from forge_runner import config, dataset
from forge_runner.dataset import TOOL_RESPONSE_JOIN, InvalidDataset, convert_example, convert_file
from forge_runner.engine import CommandEngine
from forge_runner.job import Job
from forge_runner.supervisor import run_job

TOOLS = [{"type": "function", "function": {"name": "get_weather",
          "parameters": {"type": "object", "properties": {"city": {"type": "string"}}}}}]


def _call(cid, city):
    return {"id": cid, "type": "function",
            "function": {"name": "get_weather", "arguments": json.dumps({"city": city})}}


TOOL_EXAMPLE = {"tools": TOOLS, "messages": [
    {"role": "system", "content": "Be brief."},
    {"role": "user", "content": "Weather in Paris and Rome?"},
    {"role": "assistant", "content": None, "tool_calls": [_call("c1", "Paris"), _call("c2", "Rome")]},
    {"role": "tool", "tool_call_id": "c1", "content": '{"temp_c": 18}'},
    {"role": "tool", "tool_call_id": "c2", "content": '{"temp_c": 24}'},
    {"role": "assistant", "content": "Paris 18 °C, Rome 24 °C."}]}


def test_plain_chat_keeps_its_shape():
    ex = {"messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]}
    assert convert_example(ex) == {"messages": ex["messages"], "tools": ""}


def test_tool_calls_become_function_and_observation_turns():
    row = convert_example(TOOL_EXAMPLE)
    roles = [m["role"] for m in row["messages"]]
    # Alternation the engine requires: user-side, assistant-side, ...
    assert roles == ["system", "user", "function_call", "tool", "assistant"]
    # Arguments are parsed objects, not the OpenAI string encoded twice.
    assert json.loads(row["messages"][2]["content"]) == [
        {"name": "get_weather", "arguments": {"city": "Paris"}},
        {"name": "get_weather", "arguments": {"city": "Rome"}}]
    assert row["messages"][3]["content"] == '{"temp_c": 18}' + TOOL_RESPONSE_JOIN + '{"temp_c": 24}'
    assert json.loads(row["tools"]) == TOOLS


def test_bad_arguments_are_refused_with_the_line(tmp_path):
    src = tmp_path / "in.jsonl"
    bad = json.loads(json.dumps(TOOL_EXAMPLE))
    bad["messages"][2]["tool_calls"][0]["function"]["arguments"] = "{city: Paris}"
    src.write_text(json.dumps(TOOL_EXAMPLE) + "\n\n" + json.dumps(bad) + "\n", encoding="utf-8")
    with pytest.raises(InvalidDataset, match="line 3"):
        convert_file(src, tmp_path / "out.jsonl")


def test_convert_file_writes_one_row_per_example(tmp_path):
    src, dst = tmp_path / "in.jsonl", tmp_path / "out.jsonl"
    src.write_text("\ufeff" + json.dumps(TOOL_EXAMPLE) + "\n" + json.dumps(TOOL_EXAMPLE) + "\n", encoding="utf-8")
    assert convert_file(src, dst) == 2
    rows = [json.loads(line) for line in dst.read_text(encoding="utf-8").splitlines()]
    assert all(set(r) == {"messages", "tools"} for r in rows)


def test_dataset_info_maps_tools_and_tool_roles(tmp_path):
    job = Job(job_id="j", source_ref="Qwen/Qwen2.5-0.5B-Instruct", dataset_path="x", chat_template="qwen")
    _cfg, info = config.build(job, tmp_path, has_validation=False)
    entry = info["train"]
    assert entry["columns"] == {"messages": "messages", "tools": "tools"}
    assert entry["tags"]["function_tag"] == "function_call" and entry["tags"]["observation_tag"] == "tool"


class _Client:
    def __init__(self, text):
        self.text, self.failures, self.uploads = text, [], []

    def download(self, path, dest):
        dest.write_text(self.text, encoding="utf-8")

    def progress(self, *a, **k):
        return False

    def upload_adapter(self, *a, **k):
        self.uploads.append(a)

    def report_failure(self, job_id, worker_id, **failure):
        self.failures.append(failure)


def _job():
    return Job(job_id="ftjob-tools", source_ref="Qwen/Qwen2.5-0.5B-Instruct",
               dataset_path="/v1/files/f/content", chat_template="qwen")


def test_supervisor_trains_on_the_converted_file(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_ENGINE_MODE", "ok")
    engine = CommandEngine([sys.executable, str(Path(__file__).parent / "fake_engine.py"), "{config}"])
    client = _Client(json.dumps(TOOL_EXAMPLE) + "\n")
    result = run_job(_job(), client, engine, tmp_path, "w", poll_interval=0.05)
    assert result.status == "succeeded", result.detail
    trained = (tmp_path / "ftjob-tools" / "data" / "train.jsonl").read_text(encoding="utf-8")
    assert '"function_call"' in trained and '"tool_calls"' not in trained


def test_unconvertible_dataset_fails_without_starting_the_engine(tmp_path):
    class Exploding:
        def start(self, *a, **k):
            raise AssertionError("engine must not start")
    client = _Client('{"messages": "not a list"}\n')
    result = run_job(_job(), client, Exploding(), tmp_path, "w")
    assert result.status == "failed"
    assert client.failures[0]["code"] == "invalid_dataset" and client.failures[0]["retryable"] is False
