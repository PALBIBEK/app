"""Keep each model call inside the window, in the middle of a turn too.

Between turns, a conversation past its limit is compacted (``context_memory``).
A single turn can outgrow the window on its own: every tool result it reads is
added to what the next call sends. Before each call, the step fits what it sends:

1. **Clear old tool results** (observation masking). The oldest results are
   replaced by a placeholder naming the call, newest first kept, until the call
   is back under 75% of the limit. Clearing in one batch, with headroom, keeps the
   prefix the same over the following calls (prompt caches hit). Nothing is
   lost: the journal holds every result, and ``history`` reopens one by its
   ``call_id``. As in Anthropic's context editing (``clear_tool_uses``: newest 3
   kept) and the JetBrains study, where masking matched LLM summaries at lower cost.
2. **Compact mid-turn**: still over, the conversation before this turn is
   summarised as between turns (the caller does it: it is journalled).
3. **Clear all but the newest result, then shorten that one** (its start and end
   kept). Past that, the call is refused as ``ContextWindowFull``.

Pure functions over the messages the call would send; the estimate is the one
``clamp_max_tokens`` uses, so a call this module passes is never refused there.
"""

from __future__ import annotations

import dataclasses
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

from supervisor.seams import ChatMessage

__all__ = ["KEEP_NEWEST", "Fitted", "clear_old_results", "estimate", "shrink_to_fit"]

KEEP_NEWEST: Final = 3
"""Tool results always shown in full in the first pass (Anthropic's default ``keep``)."""
MIN_CLEAR_CHARS: Final = 400
"""A result this short costs less than its placeholder saves."""
TARGET_SHARE: Final = 0.75
"""Clearing goes down to this share of the limit, so the next calls need none."""
_CHARS_PER_TOKEN: Final = 4  # as models.messages.estimate_context_tokens


@dataclass(frozen=True, slots=True)
class Fitted:
    messages: list[ChatMessage]
    cleared: list[str]
    tokens_before: int
    tokens_after: int
    truncated: str | None = None


def estimate(messages: Sequence[ChatMessage], tools: Sequence[dict[str, Any]]) -> int:
    """What the call costs, by the same count the model client caps the reply with."""
    chars = len(json.dumps(list(tools), default=str)) if tools else 0
    for m in messages:
        chars += len(m.content or "")
        for call in m.tool_calls or ():
            chars += len(str(call.get("name", ""))) + len(json.dumps(call.get("arguments", {}), default=str))
        for part in m.parts or ():
            if part.get("type") == "reasoning":
                chars += len(str(part.get("text", "")))
    return math.ceil(chars / _CHARS_PER_TOKEN)


def _names(messages: Sequence[ChatMessage]) -> dict[str, str]:
    return {
        str(c.get("id")): str(c.get("name", "tool"))
        for m in messages
        for c in (m.tool_calls or ())
    }


def _placeholder(name: str, call_id: str, size: int) -> str:
    return (
        f"[{name} result cleared to keep this conversation within the model's context window "
        f"({size} characters). The full result is kept: reopen it verbatim with the history tool, "
        f'{{"call_id": "{call_id}"}}.]'
    )


def _results(messages: Sequence[ChatMessage]) -> list[int]:
    return [i for i, m in enumerate(messages) if m.role == "tool" and m.tool_call_id]


def clear_old_results(
    messages: Sequence[ChatMessage],
    tools: Sequence[dict[str, Any]],
    limit: int,
    *,
    keep: int = KEEP_NEWEST,
) -> Fitted:
    """Clear the oldest tool results (all but the newest ``keep``) until under ``TARGET_SHARE`` of ``limit``.

    Nothing changes while the call fits ``limit``. The same messages always give
    the same result, and as a turn grows the cleared set only grows.
    """
    out = list(messages)
    before = estimate(out, tools)
    if before <= limit:
        return Fitted(out, [], before, before)
    target, now, names, cleared = int(limit * TARGET_SHARE), before, _names(out), []
    candidates = _results(out)
    for i in candidates[: max(len(candidates) - keep, 0)]:
        if now <= target:
            break
        m = out[i]
        size = len(m.content or "")
        if size < MIN_CLEAR_CHARS:
            continue
        call_id = str(m.tool_call_id)
        text = _placeholder(names.get(call_id, "tool"), call_id, size)
        out[i] = dataclasses.replace(m, content=text)
        now -= math.ceil((size - len(text)) / _CHARS_PER_TOKEN)
        cleared.append(call_id)
    return Fitted(out, cleared, before, estimate(out, tools))


def shrink_to_fit(
    messages: Sequence[ChatMessage], tools: Sequence[dict[str, Any]], limit: int
) -> Fitted:
    """The last resort: every result but the newest cleared, then the newest shortened to fit."""
    first = clear_old_results(messages, tools, limit, keep=1)
    out, cleared = list(first.messages), list(first.cleared)
    results = _results(out)
    if not results or estimate(out, tools) <= limit:
        return dataclasses.replace(first, tokens_after=estimate(out, tools))
    i = results[-1]
    m = out[i]
    text = m.content or ""
    over = estimate(out, tools) - int(limit * TARGET_SHARE)
    room = len(text) - over * _CHARS_PER_TOKEN
    if room < 2 * MIN_CLEAR_CHARS:  # nothing useful fits: the window is held by something else
        return Fitted(out, cleared, first.tokens_before, estimate(out, tools))
    half = room // 2
    call_id = str(m.tool_call_id)
    out[i] = dataclasses.replace(
        m,
        content=(
            f"{text[:half]}\n\n[... {len(text) - 2 * half} characters cut to fit the context window; "
            f'the full result: history {{"call_id": "{call_id}"}} ...]\n\n{text[-half:]}'
        ),
    )
    return Fitted(out, cleared, first.tokens_before, estimate(out, tools), truncated=call_id)
