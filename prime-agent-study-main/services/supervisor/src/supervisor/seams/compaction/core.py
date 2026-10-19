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
    "The messages above are a conversation to summarize. Create a structured "
    "context checkpoint that another LLM will use to continue the work.\n\n"
    "Use this EXACT format:\n\n" + _FORMAT
)

UPDATE_SUMMARIZATION_PROMPT = (
    "The messages above are NEW conversation messages to incorporate into the "
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


def _tool_call_ids(msg: dict[str, Any]) -> set[str]:
    return {str(c.get("id")) for c in _tool_calls(msg) if c.get("id")}


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
    request: list[dict[str, Any]] = [
        {"role": "system", "content": SUMMARIZATION_SYSTEM_PROMPT},
        *messages,
    ]
    if previous_summary:
        request.append({
            "role": "user",
            "content": (
                f"<previous-summary>\n{previous_summary}\n</previous-summary>\n\n"
                + UPDATE_SUMMARIZATION_PROMPT
            ),
        })
    else:
        request.append({"role": "user", "content": SUMMARIZATION_PROMPT})
    return request


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
        summary = summarizer(messages, previous_summary)
    except Exception:
        # The run goes on, but the dropped span is lost rather than summarised, so say why.
        logger.warning("compaction summary failed; dropping the span behind a notice", exc_info=True)
        return COMPACTION_NOTICE
    if not summary or not summary.strip():
        logger.warning("compaction summarizer returned nothing; dropping the span behind a notice")
        return COMPACTION_NOTICE
    return f"{SUMMARY_MARKER}\n{summary.strip()}"


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
