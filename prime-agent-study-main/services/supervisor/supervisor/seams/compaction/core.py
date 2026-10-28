"""Context compaction — orphan-safe trim + structured summarization (OpenAI-compat dicts)."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from typing import Any, Final

logger = logging.getLogger(__name__)

CHARS_PER_TOKEN = 3.5
DEFAULT_LIMIT_TOKENS = 120_000
KEEP_RECENT_MESSAGES = 8
COMPACTION_NOTICE = (
    "[Earlier turns were compacted away to stay within the context budget. "
    "Their tool output is no longer visible -- if you need details from before "
    "this point, re-derive them rather than recalling them.]"
)
SUMMARY_MARKER = "[context checkpoint]"
"""How summaries journalled before pi's framing began (still recognised)."""

SUMMARY_PREFIX = (
    "The conversation history before this point was compacted into the following summary:\n\n<summary>\n"
)
SUMMARY_SUFFIX = "\n</summary>"
"""pi's framing of a compaction summary, as the model reads it (``COMPACTION_SUMMARY_PREFIX``)."""

TOOL_RESULT_MAX_CHARS = 2000
"""A tool result in the transcript a summarizer reads keeps its first 2000 characters
(pi's ``TOOL_RESULT_MAX_CHARS``): the summary needs what happened, not every byte."""

SUMMARIZATION_SYSTEM_PROMPT = (
    "You are a context summarization assistant. Your task is to read a "
    "conversation between a user and an AI assistant, then produce a structured "
    "summary following the exact format specified.\n\n"
    "Do NOT continue the conversation. Do NOT respond to any questions in the "
    "conversation. ONLY output the structured summary."
)

SUMMARIZATION_PROMPT = """The messages above are a conversation to summarize. Create a structured context checkpoint summary that another LLM will use to continue the work.

Use this EXACT format:

## Goal
[What is the user trying to accomplish? Can be multiple items if the session covers different tasks.]

## Constraints & Preferences
- [Any constraints, preferences, or requirements mentioned by user]
- [Or "(none)" if none were mentioned]

## Progress
### Done
- [x] [Completed tasks/changes]

### In Progress
- [ ] [Current work]

### Blocked
- [Issues preventing progress, if any]

## Key Decisions
- **[Decision]**: [Brief rationale]

## Next Steps
1. [Ordered list of what should happen next]

## Critical Context
- [Any data, examples, or references needed to continue]
- [Or "(none)" if not applicable]

Keep each section concise. Preserve exact file paths, function names, and error messages."""
"""pi's prompt, word for word (``compaction.ts``)."""

UPDATE_SUMMARIZATION_PROMPT = """The messages above are NEW conversation messages to incorporate into the existing summary provided in <previous-summary> tags.

Update the existing structured summary with new information. RULES:
- PRESERVE all existing information from the previous summary
- ADD new progress, decisions, and context from the new messages
- UPDATE the Progress section: move items from "In Progress" to "Done" when completed
- UPDATE "Next Steps" based on what was accomplished
- PRESERVE exact file paths, function names, and error messages
- If something is no longer relevant, you may remove it

Use this EXACT format:

## Goal
[Preserve existing goals, add new ones if the task expanded]

## Constraints & Preferences
- [Preserve existing, add new ones discovered]

## Progress
### Done
- [x] [Include previously done items AND newly completed items]

### In Progress
- [ ] [Current work - update based on progress]

### Blocked
- [Current blockers - remove if resolved]

## Key Decisions
- **[Decision]**: [Brief rationale] (preserve all previous, add new)

## Next Steps
1. [Update based on current state]

## Critical Context
- [Preserve important context, add new if needed]

Keep each section concise. Preserve exact file paths, function names, and error messages."""

Summarizer = Callable[[list[dict[str, Any]], str | None], str]


def _role(msg: dict[str, Any]) -> str:
    return str(msg.get("role") or "")


def _content(msg: dict[str, Any]) -> str:
    raw = msg.get("content")
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        return json.dumps(raw, ensure_ascii=False)
    return str(raw or "")


def _tool_calls(msg: dict[str, Any]) -> list[dict[str, Any]]:
    calls = msg.get("tool_calls")
    return calls if isinstance(calls, list) else []


def _is_system(msg: dict[str, Any]) -> bool:
    return _role(msg) == "system"


def _is_user(msg: dict[str, Any]) -> bool:
    return _role(msg) == "user"


def _is_assistant(msg: dict[str, Any]) -> bool:
    return _role(msg) == "assistant"


def _is_tool_result(msg: dict[str, Any]) -> bool:
    return _role(msg) == "tool" or bool(msg.get("tool_call_id"))


def _tool_call_id(msg: dict[str, Any]) -> str:
    return str(msg.get("tool_call_id") or "")


def _wire_size(msg: dict[str, Any]) -> int:
    total = len(_content(msg))
    for call in _tool_calls(msg):
        total += len(json.dumps(call, ensure_ascii=False))
    return total


def build_summary_request(
    messages: list[dict[str, Any]],
    previous_summary: str | None = None,
) -> list[dict[str, Any]]:
    """The summary call: the span as one transcript (pi's ``serializeConversation``).

    Sent as text, not as the original messages: a span's tool results without the call
    that made them are refused by providers ("tool messages must include a
    tool_call_id"), and a model shown a live conversation tends to continue it
    rather than summarise it.
    """
    transcript = f"<conversation>\n{serialize_conversation(messages)}\n</conversation>\n\n"
    previous = without_file_operations(previous_summary) if previous_summary else ""
    if previous:
        prompt = (
            transcript
            + f"<previous-summary>\n{previous}\n</previous-summary>\n\n"
            + UPDATE_SUMMARIZATION_PROMPT
        )
    else:
        prompt = transcript + SUMMARIZATION_PROMPT
    return [
        {"role": "system", "content": SUMMARIZATION_SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]


def _text(msg: dict[str, Any]) -> str:
    """The visible text: thinking is left out (pi's ``serializeConversation``)."""
    raw = msg.get("content")
    if isinstance(raw, list):
        texts = [
            str(p.get("text") or "")
            for p in raw
            if isinstance(p, dict) and p.get("text") and p.get("type", "text") == "text"
        ]
        if texts or not raw or all(isinstance(p, dict) for p in raw):
            return "\n".join(texts)
    return _content(msg)


def _arguments(call: dict[str, Any]) -> tuple[str, dict[str, Any] | str]:
    inner = call.get("function")
    fn: dict[str, Any] = inner if isinstance(inner, dict) else call
    args = fn.get("arguments", {})
    if isinstance(args, str):
        try:
            parsed = json.loads(args)
        except ValueError:
            return str(fn.get("name", "?")), args
        args = parsed if isinstance(parsed, dict) else args
    return str(fn.get("name", "?")), args if isinstance(args, (dict, str)) else {}


def _call_text(call: dict[str, Any]) -> str:
    """``name(key=value, ...)``, each value as JSON (pi's)."""
    name, args = _arguments(call)
    if isinstance(args, str):
        return f"{name}({args})"
    shown = ", ".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in args.items())
    return f"{name}({shown})"


def _clip_result(text: str) -> str:
    if len(text) <= TOOL_RESULT_MAX_CHARS:
        return text
    return f"{text[:TOOL_RESULT_MAX_CHARS]}\n\n[... {len(text) - TOOL_RESULT_MAX_CHARS} more characters truncated]"


def serialize_conversation(messages: list[dict[str, Any]]) -> str:
    """The messages as a plain transcript a summarizer reads (and cannot continue):
    pi's ``serializeConversation``, so tool results are cut to their first 2000
    characters and thinking is left out."""
    lines: list[str] = []
    for msg in messages:
        role, text = _role(msg), _text(msg).strip()
        if role == "assistant":
            if text:
                lines.append(f"[Assistant]: {text}")
            if calls := _tool_calls(msg):
                lines.append("[Assistant tool calls]: " + "; ".join(_call_text(c) for c in calls))
        elif role == "tool":
            if text:
                lines.append(f"[Tool result]: {_clip_result(text)}")
        elif role == "system":
            if text:
                lines.append(f"[System]: {text}")
        elif text:
            lines.append(f"[User]: {text}")
    return "\n\n".join(lines)


# --- the files a summarised span touched (pi's <read-files> / <modified-files>) ---------

_READS: Final = frozenset({"read"})
_WRITES: Final = frozenset({"write", "edit"})
_FILE_LIST: Final = re.compile(r"\n*<(read-files|modified-files)>\n(.*?)\n</\1>", re.DOTALL)


def file_operations(
    messages: list[dict[str, Any]], previous_summary: str | None = None
) -> tuple[list[str], list[str]]:
    """(files only read, files written or edited) by the span's tool calls, with those
    the previous summary listed: carried from summary to summary, as pi carries them."""
    read: set[str] = set()
    modified: set[str] = set()
    for kind, body in _FILE_LIST.findall(previous_summary or ""):
        (read if kind == "read-files" else modified).update(p for p in body.splitlines() if p.strip())
    for msg in messages:
        if not _is_assistant(msg):
            continue
        for call in _tool_calls(msg):
            name, args = _arguments(call)
            path = (args.get("path") or args.get("file_path")) if isinstance(args, dict) else None
            if not isinstance(path, str) or not path:
                continue
            if name in _READS:
                read.add(path)
            elif name in _WRITES:
                modified.add(path)
    return sorted(read - modified), sorted(modified)


def format_file_operations(read: list[str], modified: list[str]) -> str:
    """pi's ``formatFileOperations``: the lists as tags after the summary, or ""."""
    sections = []
    if read:
        sections.append("<read-files>\n" + "\n".join(read) + "\n</read-files>")
    if modified:
        sections.append("<modified-files>\n" + "\n".join(modified) + "\n</modified-files>")
    return "\n\n" + "\n\n".join(sections) if sections else ""


def without_file_operations(summary: str) -> str:
    return _FILE_LIST.sub("", summary).strip()


def is_summary(text: str) -> bool:
    """Whether ``text`` is a compaction summary (pi's framing, or the older marker)."""
    return text.startswith((SUMMARY_PREFIX, SUMMARY_MARKER))


def framed_summary(summary: str) -> str:
    return f"{SUMMARY_PREFIX}{summary.strip()}{SUMMARY_SUFFIX}"


def _summary_body(content: str) -> str | None:
    if content.startswith(SUMMARY_PREFIX):
        body = content[len(SUMMARY_PREFIX) :]
        end = body.find(SUMMARY_SUFFIX)
        return (body[:end] if end != -1 else body).strip()
    if content.startswith(SUMMARY_MARKER):
        return content[len(SUMMARY_MARKER) :].strip()
    return None


def extract_previous_summary(messages: list[dict[str, Any]]) -> str | None:
    """The latest summary in ``messages``: its text inside the framing (not the notes
    the run added after it), file lists included."""
    for msg in reversed(messages):
        body = _summary_body(_text(msg) if isinstance(msg.get("content"), list) else _content(msg))
        if body is not None:
            return body
    return None


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    return int(sum(_wire_size(m) for m in messages) / CHARS_PER_TOKEN)


def needs_compaction(
    messages: list[dict[str, Any]],
    limit_tokens: int = DEFAULT_LIMIT_TOKENS,
) -> bool:
    return estimate_tokens(messages) > limit_tokens


def whole_exchanges(
    window: list[dict[str, Any]],
    full: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Extend ``window`` backwards until no tool result is orphaned."""
    if not window:
        return []

    start = len(full) - len(window)
    while start > 0 and _is_tool_result(full[start]):
        start -= 1

    kept = full[start:]
    result_ids = {_tool_call_id(m) for m in kept if _is_tool_result(m)}

    while kept and _is_assistant(kept[-1]) and _tool_calls(kept[-1]):
        if all(str(c.get("id")) in result_ids for c in _tool_calls(kept[-1])):
            break
        kept = kept[:-1]
    return kept


def compact(
    messages: list[dict[str, Any]],
    limit_tokens: int = DEFAULT_LIMIT_TOKENS,
    summarizer: Summarizer | None = None,
) -> list[dict[str, Any]]:
    """Drop the middle of the transcript in whole exchanges."""
    if not needs_compaction(messages, limit_tokens):
        return messages

    system = [m for m in messages if _is_system(m)]
    rest = [m for m in messages if not _is_system(m)]

    first_user = next((m for m in rest if _is_user(m)), None)
    head: list[dict[str, Any]] = [first_user] if first_user else []

    tail = whole_exchanges(rest[-KEEP_RECENT_MESSAGES:], rest)
    if first_user is not None and first_user in tail:
        head = []

    if len(head) + len(tail) >= len(rest):
        return messages

    head_end = 0
    if head and first_user is not None:
        for i, m in enumerate(rest):
            if m is first_user:
                head_end = i + 1
                break

    dropped = rest[head_end : len(rest) - len(tail)]
    notice = _checkpoint(dropped, messages, summarizer)
    out = [*system, *head, notice, *tail]

    if needs_compaction(out, limit_tokens):
        out = [_shrink(m, limit_tokens) for m in out]
    return out


def summarize_span(
    messages: list[dict[str, Any]],
    *,
    previous_summary: str | None = None,
    summarizer: Summarizer | None = None,
) -> str:
    """Summarize a dropped span; degrades to plain notice on failure."""
    if not messages:
        return COMPACTION_NOTICE
    if summarizer is None:
        return COMPACTION_NOTICE
    try:
        summary = _fitting(summarizer, messages, previous_summary)
    except Exception:
        # The run goes on, but the dropped span is lost rather than summarised, so say why.
        logger.warning("compaction summary failed; dropping the span behind a notice", exc_info=True)
        return COMPACTION_NOTICE
    if not summary or not summary.strip():
        logger.warning("compaction summarizer returned nothing; dropping the span behind a notice")
        return COMPACTION_NOTICE
    read, modified = file_operations(messages, previous_summary)
    return framed_summary(without_file_operations(summary) + format_file_operations(read, modified))


_SPLIT_DEPTH = 10
"""How many times a span too big for the window is halved before giving up."""


def _too_big(exc: Exception) -> bool:
    """The span does not fit the model's window (``models.messages.ContextWindowFull``)."""
    return getattr(exc, "code", None) == "CONTEXT_WINDOW_FULL"


def _fitting(
    summarizer: Summarizer,
    messages: list[dict[str, Any]],
    previous: str | None,
    depth: int = 0,
) -> str:
    """Summarise ``messages``, in pieces that fit the summarizer's window.

    A span too big for one call is halved: the first half's summary is the
    previous summary the second half is folded into. A single message too big on
    its own keeps its start and end (the middle is elided).
    """
    try:
        return summarizer(messages, previous)
    except Exception as exc:
        if not _too_big(exc) or depth >= _SPLIT_DEPTH:
            raise
    if len(messages) > 1:
        mid = len(messages) // 2
        first = _fitting(summarizer, messages[:mid], previous, depth + 1)
        return _fitting(summarizer, messages[mid:], first.strip() or previous, depth + 1)
    text = _content(messages[0])
    shorter, _ = _truncate_text(text, len(text) // 2)
    return _fitting(summarizer, [{**messages[0], "content": shorter}], previous, depth + 1)


def _checkpoint(
    dropped: list[dict[str, Any]],
    full: list[dict[str, Any]],
    summarizer: Summarizer | None,
) -> dict[str, Any]:
    text = summarize_span(
        dropped,
        previous_summary=extract_previous_summary(full),
        summarizer=summarizer,
    )
    return {"role": "user", "content": text}


def _truncate_text(text: str, limit: int) -> tuple[str, bool]:
    if limit <= 0 or len(text) <= limit:
        return text, len(text) > limit if limit > 0 else False
    half = limit // 2
    elided = len(text) - (half * 2)
    return (
        f"{text[:half]}\n\n[... {elided} characters elided ...]\n\n{text[-half:]}",
        True,
    )


def _shrink(msg: dict[str, Any], limit_tokens: int) -> dict[str, Any]:
    if not _is_tool_result(msg):
        return msg
    content = _content(msg)
    budget = max(500, int(limit_tokens * CHARS_PER_TOKEN / 10))
    if len(content) <= budget:
        return msg
    body, _ = _truncate_text(content, budget)
    out = dict(msg)
    out["content"] = body
    return out
