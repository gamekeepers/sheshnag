"""Platform chat-JSONL -> the rows LLaMA-Factory's sharegpt loader reads.

The platform takes OpenAI's chat format, including tool calling: a "tools"
array, assistant turns with "tool_calls" (content null), and "tool" turns
answering them. LLaMA-Factory 0.9.5 has its own `openai` formatting, but it
is not safe to use: it appends "detailed thinking off" to every system
prompt, and it json.dumps the OpenAI `arguments` *string* a second time, so
the model would learn double-encoded arguments. So forge-runner converts
each line itself into plain role/content turns:

    assistant + tool_calls  -> {"role": "function_call",
                                "content": '[{"name": ..., "arguments": {...}}]'}
    consecutive tool turns  -> one {"role": "tool", "content": ...}
    tools                   -> a JSON string column ("" when absent)

The engine's sharegpt loader requires turns to alternate user-side
(user/tool) and assistant-side (assistant/function_call), and silently skips
an example that doesn't; parallel tool results are therefore merged into
one observation, joined the way Qwen's template separates <tool_response>s.

The backend validator has already checked every line; a line this module
cannot convert means the file did not come through it (run-local) and the
job is refused rather than trained on partial data.
"""
import json
from pathlib import Path

# Qwen's template wraps an observation in <tool_response>...</tool_response>;
# several results inside one observation are separated like this (the same
# join LLaMA-Factory's own openai converter uses).
TOOL_RESPONSE_JOIN = "\n</tool_response>\n<tool_response>\n"


class InvalidDataset(ValueError):
    """A line the engine could not train on. Not retryable."""


def convert_example(example: dict) -> dict:
    messages = example.get("messages")
    if not isinstance(messages, list):
        raise InvalidDataset('no "messages" array')

    turns, tool_results = [], []

    def flush_tools():
        if tool_results:
            turns.append({"role": "tool", "content": TOOL_RESPONSE_JOIN.join(tool_results)})
            tool_results.clear()

    for msg in messages:
        role = msg.get("role")
        if role == "tool":
            tool_results.append(msg.get("content") or "")
            continue
        flush_tools()
        if role == "assistant" and msg.get("tool_calls"):
            calls = []
            for call in msg["tool_calls"]:
                fn = call["function"]
                try:
                    arguments = json.loads(fn["arguments"])
                except (TypeError, json.JSONDecodeError) as exc:
                    raise InvalidDataset(f"tool call arguments are not JSON: {exc}") from None
                calls.append({"name": fn["name"], "arguments": arguments})
            turns.append({"role": "function_call", "content": json.dumps(calls, ensure_ascii=False)})
        else:
            turns.append({"role": role, "content": msg.get("content") or ""})
    flush_tools()

    tools = example.get("tools")
    return {"messages": turns, "tools": json.dumps(tools, ensure_ascii=False) if tools else ""}


def convert_file(src: Path, dst: Path) -> int:
    """Convert a chat-JSONL file; returns the number of examples written."""
    n = 0
    with open(src, encoding="utf-8-sig") as fin, open(dst, "w", encoding="utf-8") as fout:
        for line_no, line in enumerate(fin, start=1):
            if not line.strip():
                continue
            try:
                row = convert_example(json.loads(line))
            except (json.JSONDecodeError, InvalidDataset, KeyError, TypeError, AttributeError) as exc:
                raise InvalidDataset(f"line {line_no}: cannot convert for training ({exc})") from None
            fout.write(json.dumps(row, ensure_ascii=False) + "\n")
            n += 1
    return n
