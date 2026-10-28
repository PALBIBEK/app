"""Context compaction — orphan-safe trim + structured summarization (OpenAI-compat dicts)."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

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

SUMMARIZATION_SYSTEM_PROMPT = (
    "You are a context summarization assistant. Your task is to read a "
    "conversation between a user and an AI assistant, then produce a structured "
    "summary following the exact format specified.\n\n"
    "Do NOT continue the conversation. Do NOT respond to any questions in the "
    "conversation. ONLY output the structured summary."
)

_FORMAT = """## Goal
[What is the user trying to accomplish? Can be multiple items.]

## Constraints & Preferences
- [Constraints, preferences or requirements the user stated, or "(none)"]

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
- [Data, examples or references needed to continue, or "(none)"]

Keep each section concise. Preserve exact file paths, function names, and error
messages."""

SUMMARIZATION_PROMPT = (
    "The conversation above is to be summarized. Create a structured "
    "context checkpoint that another LLM will use to continue the work.\n\n"
    "Use this EXACT format:\n\n" + _FORMAT
)

UPDATE_SUMMARIZATION_PROMPT = (
    "The conversation above is NEW messages to incorporate into the "
    "existing summary in <previous-summary> tags.\n\n"
    "Update the existing summary with the new information. RULES:\n"
    "- PRESERVE all existing information from the previous summary\n"
    "- ADD new progress, decisions and context from the new messages\n"
    "- UPDATE Progress: move items from \"In Progress\" to \"Done\" when completed\n"
    "- UPDATE \"Next Steps\" based on what was accomplished\n"
    "- PRESERVE exact file paths, function names and error messages\n"
    "- If something is no longer relevant, you may remove it\n\n"
    "Use this EXACT format:\n\n" + _FORMAT
)

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
    if previous_summary:
        prompt = (
            transcript
            + f"<previous-summary>\n{previous_summary}\n</previous-summary>\n\n"
            + UPDATE_SUMMARIZATION_PROMPT
        )
    else:
        prompt = transcript + SUMMARIZATION_PROMPT
    return [
        {"role": "system", "content": SUMMARIZATION_SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]


def _text(msg: dict[str, Any]) -> str:
    raw = msg.get("content")
    if isinstance(raw, list):
        texts = [str(p.get("text") or "") for p in raw if isinstance(p, dict) and p.get("text")]
        if texts or not raw:
            return "\n".join(texts)
    return _content(msg)


def _call_text(call: dict[str, Any]) -> str:
    inner = call.get("function")
    fn: dict[str, Any] = inner if isinstance(inner, dict) else call
    args = fn.get("arguments", {})
    return f"{fn.get('name', '?')}({args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)})"


def serialize_conversation(messages: list[dict[str, Any]]) -> str:
    """The messages as a plain transcript a summarizer reads (and cannot continue)."""
    lines: list[str] = []
    for msg in messages:
        role, text = _role(msg), _text(msg).strip()
        if role == "assistant":
            if text:
                lines.append(f"[Assistant]: {text}")
            if calls := _tool_calls(msg):
                lines.append("[Assistant tool calls]: " + "; ".join(_call_text(c) for c in calls))
        elif role == "tool":
            lines.append(f"[Tool result]: {text}")
        elif role == "system":
            lines.append(f"[System]: {text}")
        else:
            lines.append(f"[User]: {text}")
    return "\n\n".join(lines)


def extract_previous_summary(messages: list[dict[str, Any]]) -> str | None:
    for msg in reversed(messages):
        content = _content(msg)
        if content.startswith(SUMMARY_MARKER):
            return content[len(SUMMARY_MARKER) :].strip()
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
    return f"{SUMMARY_MARKER}\n{summary.strip()}"


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
