"""
Finetune dataset validator — chat-JSONL in; a verdict, token counts and a
length histogram out.

Format (one JSON object per line, UTF-8):
    {"messages": [{"role": "system", "content": "..."},      # optional, first only
                  {"role": "user", "content": "..."},
                  {"role": "assistant", "content": "..."}]}

The same "messages" shape /v1/chat/completions batch requests already use,
and the shape OpenAI's fine-tuning API takes, so a user who has written one
has effectively written the other.

Tool calling uses the same OpenAI shape (#194): a top-level "tools" array of
function schemas, assistant turns with "tool_calls" (content null), and
"tool" turns answering them by "tool_call_id". forge-runner converts these
lines to LLaMA-Factory's sharegpt layout; the checks below are exactly what
that conversion and the engine need, so a line that validates also trains.

Every error carries a `hint` that says how to fix it, not just what is
wrong — the common wrong formats (prompt/completion pairs, ShareGPT
"conversations", a JSON array instead of JSONL, pretty-printed records) are
recognised by name. The pure part, validate_dataset(), takes its token
counter as an argument so it can be tested without a tokenizer download.
"""
import json
import logging
import math
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from database import SessionLocal
from finetune_contract import TOOL_CALLING_TEMPLATES
from models import FinetuneJob, File as FileModel, ModelCatalog
from services.finetune_events import emit_event, transition

logger = logging.getLogger(__name__)


MAX_EXAMPLES = 50_000
MIN_EXAMPLES = 10  # same floor as OpenAI; fewer cannot teach a LoRA anything measurable
MAX_FILE_BYTES = 100 * 1024 * 1024
MAX_STORED_ERRORS = 100

VALID_ROLES = ("system", "user", "assistant", "tool")
TOP_LEVEL_KEYS = frozenset({"messages", "tools"})
# Fields each role may carry (OpenAI chat format).
MESSAGE_KEYS = {
    "system":    frozenset({"role", "content"}),
    "user":      frozenset({"role", "content"}),
    "assistant": frozenset({"role", "content", "tool_calls"}),
    "tool":      frozenset({"role", "content", "tool_call_id"}),
}
_DEFAULT_MESSAGE_KEYS = frozenset({"role", "content"})

# The tool-use preamble a template adds to the system prompt when an example
# has tools (Qwen's "# Tools ... <tools></tools> ... <tool_call>" block is
# ~80 tokens), plus the per-call <tool_call>...</tool_call> wrapper.
# Approximate, like TEMPLATE_TOKENS_PER_MESSAGE.
TOOL_PROMPT_TOKENS = 80
TOKENS_PER_TOOL_CALL = 5

# Chat-template overhead per message (Qwen's <|im_start|>role\n ... <|im_end|>\n
# is 5 tokens). Approximate across templates — it moves the histogram by a
# few tokens per turn, which is well inside a bucket.
TEMPLATE_TOKENS_PER_MESSAGE = 5

# Upper bounds of the length histogram buckets; None = everything above.
HISTOGRAM_BUCKETS = (128, 256, 512, 1024, 2048, 4096, 8192, None)

# Roles people actually write instead of the three valid ones.
_ROLE_SUGGESTIONS = {
    "human": "user", "prompt": "user", "question": "user", "instruction": "user",
    "gpt": "assistant", "bot": "assistant", "model": "assistant", "ai": "assistant",
    "answer": "assistant", "response": "assistant", "chatgpt": "assistant",
    "function": "tool", "observation": "tool", "function_response": "tool",
}

_TOOL_EXAMPLE = ('{"type": "function", "function": {"name": "get_weather", '
                 '"parameters": {"type": "object", "properties": {"city": {"type": "string"}}}}}')

_EXAMPLE_LINE = ('{"messages": [{"role": "user", "content": "..."}, '
                 '{"role": "assistant", "content": "..."}]}')


TokenCounter = Callable[[str], int]


@dataclass(frozen=True)
class ValidationError:
    code: str
    line: Optional[int]
    field: Optional[str]
    message: str
    hint: str

    def as_dict(self) -> dict:
        return {"line": self.line, "code": self.code, "field": self.field,
                "message": self.message, "hint": self.hint}


@dataclass
class ValidationResult:
    valid: bool = True
    n_lines: int = 0
    errors: List[ValidationError] = field(default_factory=list)
    total_error_count: int = 0
    token_lengths: List[int] = field(default_factory=list)  # one per valid example
    n_tool_examples: int = 0          # valid examples that use tools
    first_tool_line: Optional[int] = None

    def add(self, code, line, field_, message, hint) -> None:
        self.valid = False
        self.total_error_count += 1
        if len(self.errors) < MAX_STORED_ERRORS:
            self.errors.append(ValidationError(code, line, field_, message, hint))

    def stats(self, max_seq_length: int, token_count_method: str) -> dict:
        lengths = sorted(self.token_lengths)
        n = len(lengths)
        histogram, lower = [], 0
        for upper in HISTOGRAM_BUCKETS:
            count = sum(1 for x in lengths if x > lower and (upper is None or x <= upper))
            histogram.append({"gt": lower, "le": upper, "count": count})
            if upper is not None:
                lower = upper

        def pct(p):
            return lengths[min(n - 1, int(p * (n - 1)))] if n else None

        return {
            "n_examples": n,
            "total_tokens": sum(lengths),
            "token_count_method": token_count_method,
            "min": lengths[0] if n else None,
            "p50": pct(0.50),
            "p95": pct(0.95),
            "max": lengths[-1] if n else None,
            "max_seq_length": max_seq_length,
            "n_truncated": sum(1 for x in lengths if x > max_seq_length),
            "n_tool_examples": self.n_tool_examples,
            "histogram": histogram,
        }


# ─── Per-line checks ───────────────────────────────────────────────────────

def _check_wrong_format(entry: dict, line: int, result: ValidationResult) -> bool:
    """Recognise the common *other* dataset formats and say how to convert.
    Returns True if one matched (the line's other checks are then noise)."""
    keys = set(entry)
    if {"prompt", "completion"} <= keys:
        result.add("wrong_format", line, None,
                   "prompt/completion pairs are not supported",
                   'convert each line to {"messages": [{"role": "user", "content": <prompt>}, '
                   '{"role": "assistant", "content": <completion>}]}')
        return True
    if {"instruction", "output"} <= keys:
        result.add("wrong_format", line, None,
                   "Alpaca-style instruction/input/output records are not supported",
                   'convert each line to {"messages": [{"role": "user", "content": <instruction + "\\n\\n" + input>}, '
                   '{"role": "assistant", "content": <output>}]}')
        return True
    if "conversations" in keys and "messages" not in keys:
        result.add("wrong_format", line, "conversations",
                   'ShareGPT-style "conversations" with from/value is not supported',
                   'rename "conversations" to "messages", and in each turn rename "from" to "role" '
                   '(human -> user, gpt -> assistant, system -> system) and "value" to "content"')
        return True
    return False


def _check_tools(entry: dict, line: int, result: ValidationResult) -> Optional[set]:
    """Validate the optional top-level "tools" array. Returns the declared
    function names (empty if there is no "tools"), or None if it is invalid."""
    if "tools" not in entry:
        return set()
    tools = entry["tools"]
    if not isinstance(tools, list) or not tools:
        result.add("invalid_tools", line, "tools", '"tools" is not a non-empty array',
                   f'"tools" lists the functions the model may call, e.g. [{_TOOL_EXAMPLE}]; '
                   "remove it if this example calls none")
        return None
    names, ok = set(), True
    for k, tool in enumerate(tools):
        where = f"tools[{k}]"
        fn = tool.get("function") if isinstance(tool, dict) else None
        if (not isinstance(tool, dict) or tool.get("type") != "function"
                or not isinstance(fn, dict)):
            result.add("invalid_tool", line, where,
                       f'{where} is not a {{"type": "function", "function": {{...}}}} object',
                       f"each tool must look like {_TOOL_EXAMPLE}")
            ok = False
            continue
        name = fn.get("name")
        if not isinstance(name, str) or not name.strip():
            result.add("invalid_tool", line, f"{where}.function.name",
                       f"{where}.function.name is missing or empty",
                       "give every function the name the model will call it by")
            ok = False
            continue
        if "parameters" in fn and not isinstance(fn["parameters"], dict):
            result.add("invalid_tool", line, f"{where}.function.parameters",
                       f"{where}.function.parameters is not an object",
                       'parameters must be a JSON Schema object, e.g. {"type": "object", '
                       '"properties": {...}}')
            ok = False
        if name in names:
            result.add("duplicate_tool", line, f"{where}.function.name",
                       f"function {name!r} is declared twice in tools",
                       "declare each function once")
            ok = False
        names.add(name)
    return names if ok else None


def _check_tool_calls(msg: dict, where: str, declared: Optional[set], line: int,
                      result: ValidationResult) -> dict:
    """Validate an assistant turn's tool_calls. Returns {call id: name} for
    the calls whose id is usable, so later tool turns can be matched."""
    calls = msg["tool_calls"]
    if not isinstance(calls, list) or not calls:
        result.add("invalid_tool_calls", line, f"{where}.tool_calls",
                   f"{where}.tool_calls is not a non-empty array",
                   "remove tool_calls from a turn that calls no tool, or list the calls")
        return {}
    content = msg.get("content")
    if content is not None and not (isinstance(content, str) and not content.strip()):
        result.add("text_with_tool_calls", line, f"{where}.content",
                   f"{where} has both text content and tool_calls",
                   "set content to null on a turn that calls tools; the training engine "
                   "cannot learn text and a tool call in the same turn yet. Put the text in "
                   "the assistant turn after the tool results instead")
    ids = {}
    for j, call in enumerate(calls):
        cw = f"{where}.tool_calls[{j}]"
        fn = call.get("function") if isinstance(call, dict) else None
        if not isinstance(call, dict) or call.get("type") != "function" or not isinstance(fn, dict):
            result.add("invalid_tool_call", line, cw,
                       f'{cw} is not a {{"id", "type": "function", "function": {{...}}}} object',
                       'each call must look like {"id": "call_1", "type": "function", "function": '
                       '{"name": "get_weather", "arguments": "{\\"city\\": \\"Paris\\"}"}}')
            continue
        call_id = call.get("id")
        if not isinstance(call_id, str) or not call_id.strip():
            result.add("missing_tool_call_id", line, f"{cw}.id", f"{cw}.id is missing or empty",
                       'give every call an id (e.g. "call_1") and answer it with a tool turn '
                       "carrying the same tool_call_id")
        elif call_id in ids:
            result.add("duplicate_tool_call_id", line, f"{cw}.id",
                       f"{cw}.id {call_id!r} is used twice in the same turn",
                       "give each call in a turn its own id")
        name = fn.get("name")
        if not isinstance(name, str) or not name.strip():
            result.add("invalid_tool_call", line, f"{cw}.function.name",
                       f"{cw}.function.name is missing or empty", "name the function being called")
        elif declared is not None and name not in declared:
            known = f"declared: {sorted(declared)}" if declared else 'this line has no "tools"'
            result.add("undeclared_function", line, f"{cw}.function.name",
                       f"{cw} calls {name!r}, which is not declared in tools ({known})",
                       f'add its schema to this line\'s "tools", e.g. {_TOOL_EXAMPLE}, '
                       "or fix the name")
        args = fn.get("arguments")
        if isinstance(args, (dict, list)):
            result.add("invalid_arguments", line, f"{cw}.function.arguments",
                       f"{cw}.function.arguments is a JSON {type(args).__name__}, not a string",
                       "arguments must be a JSON-encoded string, as in the OpenAI format; "
                       "in Python: json.dumps(args)")
        else:
            try:
                parsed = json.loads(args) if isinstance(args, str) else None
            except json.JSONDecodeError as exc:
                parsed, detail = None, f" ({exc.msg})"
            else:
                detail = ""
            if not isinstance(parsed, dict):
                result.add("invalid_arguments", line, f"{cw}.function.arguments",
                           f"{cw}.function.arguments is not a JSON object string{detail}",
                           'arguments must be a JSON object encoded as a string, e.g. '
                           '"{\\"city\\": \\"Paris\\"}" — use "{}" for a call with no arguments')
        if isinstance(call_id, str) and call_id.strip() and call_id not in ids:
            ids[call_id] = name
    return ids


def _check_messages(entry: dict, line: int, result: ValidationResult) -> Optional[tuple]:
    """Validate the messages array (and tools). Returns (messages, tools) if
    the line is usable."""
    errors_before = result.total_error_count

    unknown = sorted(set(entry) - TOP_LEVEL_KEYS)
    if unknown:
        result.add("unknown_fields", line, None,
                   f"unknown top-level field(s) {unknown}",
                   'only "messages" and "tools" are read; remove the other fields, '
                   "or put the text they hold into a message")

    declared = _check_tools(entry, line, result)

    messages = entry.get("messages")
    if not isinstance(messages, list):
        result.add("missing_messages", line, "messages",
                   '"messages" is missing or is not an array',
                   f"every line needs a messages array, e.g. {_EXAMPLE_LINE}")
        return None
    if len(messages) < 2:
        result.add("too_few_messages", line, "messages",
                   f"messages has {len(messages)} turn(s); at least 2 are needed",
                   "each example needs at least one user turn followed by the assistant "
                   "reply the model should learn to produce")
        return None

    uses_tools = "tools" in entry
    pending: dict = {}        # calls from the last tool-calling turn not yet answered
    prev = None               # "tool_call" | "tool" | role of the previous turn
    for i, msg in enumerate(messages):
        where = f"messages[{i}]"
        if not isinstance(msg, dict):
            result.add("invalid_message", line, where, f"{where} is not an object",
                       'each turn must be an object like {"role": "user", "content": "..."}')
            prev = None
            continue

        role = msg.get("role")
        allowed = MESSAGE_KEYS.get(role, _DEFAULT_MESSAGE_KEYS)
        # An unknown role is the root cause; don't also flag fields that
        # would be fine on the role it should have been.
        extra = sorted(set(msg) - allowed) if role in VALID_ROLES else []
        if extra:
            if "tool_calls" in extra:
                hint = "only assistant turns can carry tool_calls"
            elif "tool_call_id" in extra:
                hint = 'only "tool" turns carry tool_call_id'
            else:
                hint = f"a {role or 'message'} turn may only have {sorted(allowed)}; remove the others"
            result.add("unknown_fields", line, where, f"{where} has unknown field(s) {extra}", hint)

        if role not in VALID_ROLES:
            suggestion = _ROLE_SUGGESTIONS.get(str(role).lower()) if role is not None else None
            hint = (f'change "{role}" to "{suggestion}"' if suggestion
                    else f"role must be one of {list(VALID_ROLES)}")
            result.add("invalid_role", line, f"{where}.role",
                       f"{where}.role is {role!r}", hint)
        elif role == "system" and i != 0:
            result.add("misplaced_system", line, f"{where}.role",
                       f"{where} is a system message but is not the first turn",
                       "move the system message to the start of the conversation, or merge it "
                       "into the first user turn")

        if pending and role != "tool":
            result.add("unanswered_tool_call", line, where,
                       f"tool call(s) {sorted(pending)} are not answered before {where}",
                       "after an assistant turn with tool_calls, add one tool turn per call "
                       '({"role": "tool", "tool_call_id": <the call\'s id>, "content": <result>}) '
                       "before the next user or assistant turn")
            pending = {}

        is_tool_call = role == "assistant" and "tool_calls" in msg
        if is_tool_call:
            uses_tools = True
            pending = _check_tool_calls(msg, where, declared, line, result)
        else:
            content = msg.get("content")
            if isinstance(content, list):
                result.add("invalid_content", line, f"{where}.content",
                           f"{where}.content is an array of parts",
                           "content must be a plain string; join text parts into one string "
                           "(image and audio parts are not supported for finetuning)")
            elif not isinstance(content, str) or not content.strip():
                result.add("empty_content", line, f"{where}.content",
                           f"{where}.content is missing or empty",
                           "every turn needs non-empty text content; drop the turn or fill it in")

        if role == "tool":
            uses_tools = True
            call_id = msg.get("tool_call_id")
            if prev not in ("tool_call", "tool"):
                result.add("misplaced_tool", line, where,
                           f"{where} is a tool result but does not follow an assistant tool call",
                           "a tool turn must come right after the assistant turn whose "
                           "tool_calls it answers (or after another tool turn for the same calls)")
            elif not isinstance(call_id, str) or call_id not in pending:
                open_ids = sorted(pending) or "none left"
                result.add("unknown_tool_call_id", line, f"{where}.tool_call_id",
                           f"{where}.tool_call_id {call_id!r} does not answer a pending call "
                           f"(pending: {open_ids})",
                           "set tool_call_id to the id of the call this result answers; each "
                           "call is answered exactly once")
            else:
                pending.pop(call_id)

        prev = "tool_call" if is_tool_call else role

    roles = [m.get("role") for m in messages if isinstance(m, dict)]
    if "assistant" not in roles:
        result.add("no_assistant", line, "messages",
                   "no assistant turn",
                   "add the assistant reply the model should learn to produce — without one "
                   "the example teaches nothing")
    elif roles and roles[-1] != "assistant":
        result.add("last_not_assistant", line, f"messages[{len(messages) - 1}].role",
                   f"the last turn is {roles[-1]!r}, not 'assistant'",
                   "end each example with the assistant reply (or tool call); trailing user "
                   "or tool turns have no answer to train on")

    if result.total_error_count == errors_before:
        _check_alternation(messages, line, result)

    if uses_tools and result.first_tool_line is None:
        result.first_tool_line = line
    if result.total_error_count != errors_before:
        return None
    return messages, (entry.get("tools") or [])


def _check_alternation(messages: list, line: int, result: ValidationResult) -> None:
    """The engine trains on user -> assistant pairs and silently drops an
    example whose turns don't alternate, so refuse it here. Consecutive tool
    results count as one user-side turn (they are merged into one)."""
    expect_user = True
    for i, msg in enumerate(messages):
        role = msg["role"]
        if role == "system":
            continue
        if role == "tool" and i > 0 and messages[i - 1]["role"] == "tool":
            continue
        user_side = role in ("user", "tool")
        if user_side != expect_user:
            result.add("roles_not_alternating", line, f"messages[{i}].role",
                       f"messages[{i}] is a second {'user' if user_side else 'assistant'}-side "
                       "turn in a row",
                       "turns must alternate user -> assistant -> user ...; merge consecutive "
                       "user turns into one and consecutive assistant turns into one "
                       "(a tool result counts as a user-side turn)")
            return
        expect_user = not expect_user


def _count_example_tokens(messages: list, tools: list, count_tokens: TokenCounter) -> int:
    """Everything the model sees: turn text, tool calls as rendered
    (name + parsed arguments), tool results, and the tool schemas."""
    total = 0
    if tools:
        total += TOOL_PROMPT_TOKENS + sum(
            count_tokens(json.dumps(t, ensure_ascii=False)) for t in tools)
    for m in messages:
        total += TEMPLATE_TOKENS_PER_MESSAGE
        if isinstance(m.get("content"), str) and m["content"]:
            total += count_tokens(m["content"])
        for call in m.get("tool_calls") or []:
            fn = call["function"]
            rendered = {"name": fn["name"], "arguments": json.loads(fn["arguments"])}
            total += TOKENS_PER_TOOL_CALL + count_tokens(json.dumps(rendered, ensure_ascii=False))
    return total


# ─── The pure validator ────────────────────────────────────────────────────

def validate_dataset(filepath: str, count_tokens: TokenCounter, *,
                     min_examples: int = MIN_EXAMPLES,
                     tools_unsupported: Optional[str] = None) -> ValidationResult:
    """Validate a chat-JSONL file. Never raises on bad input — every problem
    becomes a ValidationError with a hint.

    `tools_unsupported`, when set, is the hint for a dataset that uses tool
    calling on a base whose chat template has no tool-call format."""
    result = ValidationResult()

    try:
        with open(filepath, "rb") as fh:
            raw = fh.read(MAX_FILE_BYTES + 1)
    except FileNotFoundError:
        result.add("file_not_found", None, None, "the uploaded file is missing on the server",
                   "upload the file again with POST /v1/files and use the new id")
        return result

    if len(raw) > MAX_FILE_BYTES:
        result.add("file_too_large", None, None,
                   f"file is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB",
                   "split the dataset or drop the longest examples")
        return result

    try:
        text = raw.decode("utf-8-sig")  # tolerate a BOM; Excel/Notepad add one
    except UnicodeDecodeError as exc:
        line = raw[:exc.start].count(b"\n") + 1
        result.add("not_utf8", line, None,
                   f"file is not valid UTF-8 (byte {exc.start})",
                   "re-save the file as UTF-8 (in Python: open(path, 'w', encoding='utf-8'))")
        return result

    if text.lstrip().startswith("["):
        try:
            if isinstance(json.loads(text), list):
                result.add("json_array", None, None,
                           "file is a single JSON array, not JSON Lines",
                           "write one JSON object per line instead of one array — in Python: "
                           "for ex in data: f.write(json.dumps(ex) + '\\n')")
                return result
        except json.JSONDecodeError:
            pass

    for line_no, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue

        result.n_lines += 1
        if result.n_lines > MAX_EXAMPLES:
            result.add("too_many_examples", line_no, None,
                       f"more than {MAX_EXAMPLES} examples",
                       "split the dataset across jobs or sample it down")
            break

        try:
            entry = json.loads(stripped)
        except json.JSONDecodeError as exc:
            if stripped in ("{", "}", "},") or (stripped.startswith("{") and not stripped.endswith("}")):
                hint = ("this looks like one record pretty-printed across several lines; "
                        "each example must be on a single line (json.dumps without indent=)")
            else:
                hint = ("each line must be one complete JSON object — check for a trailing comma, "
                        "single quotes instead of double quotes, or an unescaped newline in a string")
            result.add("invalid_json", line_no, None, f"invalid JSON: {exc.msg} (column {exc.colno})", hint)
            continue

        if not isinstance(entry, dict):
            result.add("not_object", line_no, None,
                       f"line is a JSON {type(entry).__name__}, not an object",
                       f"each line must be an object like {_EXAMPLE_LINE}")
            continue

        if _check_wrong_format(entry, line_no, result):
            continue

        checked = _check_messages(entry, line_no, result)
        if checked is not None:
            messages, tools = checked
            result.token_lengths.append(_count_example_tokens(messages, tools, count_tokens))
            if tools or any(m.get("tool_calls") or m.get("role") == "tool" for m in messages):
                result.n_tool_examples += 1

    if tools_unsupported and result.first_tool_line is not None:
        result.add("tools_unsupported_base", result.first_tool_line, None,
                   "this dataset uses tool calling, which the job's base model cannot be "
                   "trained on", tools_unsupported)

    if result.n_lines == 0:
        result.add("empty_file", None, None, "file contains no examples",
                   f"add at least {min_examples} lines like {_EXAMPLE_LINE}")
    elif result.valid and result.n_lines < min_examples:
        result.add("too_few_examples", None, None,
                   f"{result.n_lines} example(s); at least {min_examples} are needed",
                   f"add more examples — {min_examples} is the minimum, a few hundred is typical "
                   "for a LoRA to learn a style or format")

    return result


# ─── Token counting ────────────────────────────────────────────────────────

_token_counters: dict = {}


def load_token_counter(source_ref: str, revision: Optional[str]) -> tuple[TokenCounter, str]:
    """The base model's own tokenizer when it can be fetched, else a
    chars/4 estimate. Returns (counter, method) — the method is recorded in
    the stats so an estimate is never mistaken for an exact count. Only
    successes are cached, so a transient hub outage is retried next job."""
    key = (source_ref, revision)
    if key in _token_counters:
        return _token_counters[key]
    try:
        from tokenizers import Tokenizer
        tok = Tokenizer.from_pretrained(source_ref, revision=revision or "main")
        counter = (lambda s: len(tok.encode(s, add_special_tokens=False).ids),
                   f"tokenizer:{source_ref}@{revision or 'main'}")
        _token_counters[key] = counter
        return counter
    except Exception as exc:  # no network, gated repo, tokenizers not installed
        logger.warning("Tokenizer for %s unavailable (%s) — estimating tokens", source_ref, exc)
        return (lambda s: max(1, math.ceil(len(s) / 4))), "estimate:chars/4"


# ─── Job integration ───────────────────────────────────────────────────────

def _summarise(errors: List[ValidationError], total: int) -> str:
    first = errors[0]
    where = f"line {first.line}: " if first.line else ""
    more = f" (and {total - 1} more)" if total > 1 else ""
    return f"{where}{first.message} — fix: {first.hint}{more}"


def _tools_unsupported_hint(db, base_model_id: str) -> Optional[str]:
    """None if the base can be trained on tool calls, else the fix to show."""
    entry = db.query(ModelCatalog).filter(ModelCatalog.id == base_model_id).first()
    template = entry.chat_template if entry else None
    if template in TOOL_CALLING_TEMPLATES:
        return None
    able = sorted(e.id for e in db.query(ModelCatalog).filter(
        ModelCatalog.source_type == "huggingface",
        ModelCatalog.enabled.is_(True),
        ModelCatalog.status == "active",
        ModelCatalog.chat_template.in_(TOOL_CALLING_TEMPLATES),
    ))
    return (f"'{base_model_id}' uses the '{template}' chat template, which has no tool-call "
            f"format; submit the job on one of {able or 'no base yet'}, or remove the "
            "tools, tool_calls and tool turns from the dataset")


def validate_finetune_job(job_id: str, session_factory=None, token_counter_loader=None) -> bool:
    """Validate a job's training (and validation) file, record stats and
    events, and move the job to queued or failed. Returns True if queued.
    Defaults are looked up at call time so tests can patch the module."""
    db = (session_factory or SessionLocal)()
    token_counter_loader = token_counter_loader or load_token_counter
    try:
        job = db.query(FinetuneJob).filter(FinetuneJob.id == job_id).first()
        if not job or job.status != "validating_files":
            return False

        count_tokens, method = token_counter_loader(job.base_source_ref, job.base_source_revision)
        tools_unsupported = _tools_unsupported_hint(db, job.base_model_id)

        for param, file_id in (("training_file", job.training_file_id),
                               ("validation_file", job.validation_file_id)):
            if not file_id:
                continue
            f = db.query(FileModel).filter(FileModel.id == file_id).first()
            result = validate_dataset(f.filepath if f else "", count_tokens,
                                      min_examples=MIN_EXAMPLES if param == "training_file" else 1,
                                      tools_unsupported=tools_unsupported)
            if not result.valid:
                job.error = json.dumps({
                    "code": f"invalid_{param}",
                    "param": param,
                    "message": _summarise(result.errors, result.total_error_count),
                    "details": [e.as_dict() for e in result.errors],
                    "total_errors": result.total_error_count,
                })
                transition(db, job, "failed",
                           f"{param} failed validation with {result.total_error_count} error(s): "
                           f"{_summarise(result.errors, result.total_error_count)}",
                           level="error")
                db.commit()
                return False

            stats = result.stats(job.max_seq_length, method)
            if param == "training_file":
                job.training_file_stats = json.dumps(stats)
            emit_event(db, job.id,
                       f"{param} validated: {stats['n_examples']} examples, "
                       f"{stats['total_tokens']} tokens ({method})",
                       data={"param": param, **stats})
            if stats["n_truncated"]:
                emit_event(db, job.id,
                           f"{stats['n_truncated']} example(s) in {param} are longer than "
                           f"max_seq_length={job.max_seq_length} and will be truncated",
                           level="warn")

        transition(db, job, "queued", "Job queued for training")
        db.commit()
        return True

    except Exception:
        logger.exception("Validation crashed for finetune job %s", job_id)
        db.rollback()
        job = db.query(FinetuneJob).filter(FinetuneJob.id == job_id).first()
        if job and job.status == "validating_files":
            job.error = json.dumps({"code": "validation_error", "param": None,
                                    "message": "internal error while validating files; retry the job"})
            transition(db, job, "failed", "Validation failed with an internal error", level="error")
            db.commit()
        return False
    finally:
        db.close()
