"""Prompt cache breakpoints.

Compaction bounds how big the transcript gets; caching makes re-sending what
survives cheap. Two breakpoints do nearly all the work:

1. **The system prompt**, byte-identical on every turn.
2. **A rolling breakpoint on the last message**, so turn N reads turns
   1..N-1 from cache rather than paying full input price for them again.

Marking only the *last* message rather than several is deliberate: Anthropic
allows at most four `cache_control` blocks per request, and a breakpoint at
the tail already subsumes every prefix before it.
"""

from __future__ import annotations

import copy
from typing import Any

CACHE_CONTROL = {"type": "ephemeral"}

MIN_CACHEABLE_CHARS = 7000
"""Anthropic ignores a breakpoint below its minimum cacheable prefix (1024
tokens for Sonnet/Opus, 2048 for Haiku) and bills the attempt as a cache
*write* at 1.25x -- so marking something small is strictly worse than leaving
it alone. 2048 tokens at ~3.5 chars/token is the conservative floor that
holds for the whole family."""


def supports_caching(model: str) -> bool:
    """Name check, not a live capability probe.

    Fails closed. Sending `cache_control` to a provider that does not
    understand it is not uniformly harmless -- some ignore it, others reject
    the request -- and a cost optimisation that can take the run down is not
    worth it. The cost of a false negative is merely the status quo.
    """
    name = model.lower()
    return "anthropic" in name or "claude" in name


def apply_cache_breakpoints(messages: list[dict[str, Any]],
                            tools_chars: int = 0) -> list[dict[str, Any]]:
    """Return a copy of `messages` with breakpoints added where worthwhile.

    `tools_chars` is the serialised size of the tool schemas. It matters
    because the cached prefix is **tools + system**, not the system prompt
    alone: a modest prompt alongside several large tool schemas clears the
    minimum comfortably, and measuring only the system prompt would skip
    caching on exactly the setups that benefit most.
    """
    out = copy.deepcopy(messages)
    if not out:
        return out

    system_chars = 0
    if out[0].get("role") == "system":
        system_chars = len(str(out[0].get("content") or ""))

    if system_chars and (system_chars + tools_chars) >= MIN_CACHEABLE_CHARS:
        out[0]["content"] = [{"type": "text", "text": out[0]["content"],
                              "cache_control": CACHE_CONTROL}]

    if len(out) > 1:
        _mark(out[-1])
    return out


def _mark(message: dict[str, Any]) -> None:
    """Attach the rolling breakpoint, if there is anything to attach it to.

    Skipped for empty content -- an assistant tool-call message carries its
    payload in `tool_calls` and has no text block, so there is nothing to
    mark and fabricating one risks a provider validation error.
    """
    content = message.get("content")
    if isinstance(content, str) and content:
        message["content"] = [{"type": "text", "text": content,
                               "cache_control": CACHE_CONTROL}]
    elif isinstance(content, list) and content:
        last = content[-1]
        if isinstance(last, dict) and last.get("type") == "text":
            last["cache_control"] = CACHE_CONTROL
