"""Keeping the transcript from growing without bound.

Without compaction every turn re-sends everything before it, so total input
cost grows with the *square* of the turn count. Measured on a predecessor
harness that omitted it: by turn 15 of a real task, most of the spend was
re-transmitting tool output nobody was still reasoning about.

**The invariant that makes this non-trivial.** OpenAI-compatible providers
require that an assistant message containing `tool_calls` is followed by one
tool result per call. Trimming naively from the front of the transcript
breaks exactly this pair -- you drop an assistant message and leave its tool
results behind -- and the provider answers 400 with a message that does not
mention compaction at all. So dropping is done in *whole exchanges*, and both
orphan directions are tested.
"""

from __future__ import annotations

from typing import Callable, Protocol

from .truncate import truncate_text
from ..types import (
    AssistantMessage,
    Message,
    SystemMessage,
    ToolResultMessage,
    UserMessage,
)

CHARS_PER_TOKEN = 3.5
"""Rough English approximation. Deliberately not a real tokenizer: this
decides *when* to compact, and being 20% off simply moves the trigger point
slightly. A tokenizer dependency would cost far more than that error."""

DEFAULT_LIMIT_TOKENS = 120_000
KEEP_RECENT_MESSAGES = 8
COMPACTION_NOTICE = (
    "[Earlier turns were compacted away to stay within the context budget. "
    "Their tool output is no longer visible -- if you need details from before "
    "this point, re-derive them rather than recalling them.]"
)

SUMMARY_MARKER = "[context checkpoint]"
"""Prefix on the injected summary. It is how a later compaction recognizes its
own previous output and updates it rather than summarizing it again."""


class Summarizer(Protocol):
    """Turns the span being dropped into a structured checkpoint.

    Deliberately a plain callable rather than an LLM client: compaction stays
    a pure function of its inputs, tests run offline, and the caller decides
    which (cheap) model does the summarizing.
    """

    def __call__(self, messages: list[Message],
                 previous_summary: str | None = None) -> str: ...


SUMMARIZATION_SYSTEM_PROMPT = (
    "You are a context summarization assistant. Your task is to read a "
    "conversation between a user and an AI assistant, then produce a structured "
    "summary following the exact format specified.\n\n"
    "Do NOT continue the conversation. Do NOT respond to any questions in the "
    "conversation. ONLY output the structured summary."
)
"""The prohibition is the load-bearing half. The summarizer is handed a real
conversation, and the strongest prior for any chat model is to answer it --
without this it replies to the last question instead of summarizing."""

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


def build_summary_request(messages: list[Message],
                          previous_summary: str | None = None) -> list[Message]:
    """The message list to send to the summarizing model.

    The instruction goes *after* the conversation rather than in the system
    prompt so the model reads the transcript as data and the ask as the most
    recent thing said -- the same reason the format spec is repeated verbatim
    in the update variant.
    """
    request: list[Message] = [SystemMessage(SUMMARIZATION_SYSTEM_PROMPT), *messages]
    if previous_summary:
        request.append(UserMessage(
            f"<previous-summary>\n{previous_summary}\n</previous-summary>\n\n"
            + UPDATE_SUMMARIZATION_PROMPT))
    else:
        request.append(UserMessage(SUMMARIZATION_PROMPT))
    return request


def build_summarizer(llm) -> Summarizer:
    """Wire a `Summarizer` to an LLM client.

    No tools are offered. The summarizer has one job, and a model given tools
    while looking at a transcript full of tool calls will start making them --
    turning a cheap single call into a second agent loop.
    """
    def summarize(messages: list[Message],
                  previous_summary: str | None = None) -> str:
        completion = llm.complete(build_summary_request(messages, previous_summary),
                                  tools=[])
        return getattr(completion.message, "content", "") or ""

    return summarize


def extract_previous_summary(messages: list[Message]) -> str | None:
    """Find a checkpoint this module injected earlier.

    Without this a long session summarizes its own summary repeatedly, and
    detail decays geometrically -- by the third compaction the original goal
    has been paraphrased three times.
    """
    for message in reversed(messages):
        content = getattr(message, "content", "") or ""
        if isinstance(content, str) and content.startswith(SUMMARY_MARKER):
            return content[len(SUMMARY_MARKER):].strip()
    return None


def estimate_tokens(messages: list[Message]) -> int:
    total = 0
    for m in messages:
        wire = m.to_wire()
        total += len(str(wire.get("content") or ""))
        for call in wire.get("tool_calls") or []:
            total += len(str(call))
    return int(total / CHARS_PER_TOKEN)


def needs_compaction(messages: list[Message], limit_tokens: int = DEFAULT_LIMIT_TOKENS) -> bool:
    return estimate_tokens(messages) > limit_tokens


def compact(messages: list[Message],
            limit_tokens: int = DEFAULT_LIMIT_TOKENS,
            summarizer: Summarizer | None = None) -> list[Message]:
    """Drop the middle of the transcript, in whole exchanges.

    Kept: every system message, the first user message (the original
    instruction -- losing it means forgetting the task), and the most recent
    exchanges. Everything between is replaced by a structured summary when a
    `summarizer` is supplied, or a bare notice when it is not.

    The summary is the difference between compaction that costs the agent its
    findings and compaction that costs it only the transcript that produced
    them. 40 messages of tool output usually reduce to five lines of
    conclusions, which is exactly what the next turn needs.
    """
    if not needs_compaction(messages, limit_tokens):
        return messages

    system = [m for m in messages if isinstance(m, SystemMessage)]
    rest = [m for m in messages if not isinstance(m, SystemMessage)]

    first_user = next((m for m in rest if isinstance(m, UserMessage)), None)
    head: list[Message] = [first_user] if first_user else []

    tail = _whole_exchanges(rest[-KEEP_RECENT_MESSAGES:], rest)
    if first_user in tail:
        head = []

    # Already minimal: compacting further would delete real content and make
    # the operation non-idempotent.
    if len(head) + len(tail) >= len(rest):
        return messages

    # Sliced by index, never by `in`. Messages compare by *value*, so a
    # transcript holding two identical messages -- a retried tool call, a
    # repeated error -- would exclude both when only one was meant, and a
    # transcript of uniform messages computes an empty dropped span and
    # silently skips summarization altogether.
    head_end = next((i + 1 for i, m in enumerate(rest) if m is first_user),
                    0) if head else 0
    dropped = rest[head_end:len(rest) - len(tail)]
    notice = _checkpoint(dropped, messages, summarizer)
    out = [*system, *head, notice, *tail]

    # Last resort: if even the kept tail busts the budget, shrink the largest
    # tool results in place rather than dropping messages and risking orphans.
    if needs_compaction(out, limit_tokens):
        out = [_shrink(m, limit_tokens) for m in out]
    return out


def _checkpoint(dropped: list[Message], full: list[Message],
                summarizer: Summarizer | None) -> Message:
    """The message that replaces the dropped span.

    Every failure path lands on the plain notice. Compaction runs when the
    session is already long and expensive, so a provider outage here must
    degrade the *quality* of what survives, never take down the run.
    """
    if summarizer is None or not dropped:
        return UserMessage(COMPACTION_NOTICE)

    try:
        summary = summarizer(dropped, previous_summary=extract_previous_summary(full))
    except Exception:  # noqa: BLE001 - any summarizer failure degrades, not fails
        return UserMessage(COMPACTION_NOTICE)

    if not summary or not summary.strip():
        return UserMessage(COMPACTION_NOTICE)
    return UserMessage(f"{SUMMARY_MARKER}\n{summary.strip()}")


def _whole_exchanges(window: list[Message], full: list[Message]) -> list[Message]:
    """Extend `window` backwards until no tool result is orphaned.

    A tool result whose originating assistant message was cut is invalid on
    the wire, so the window is grown to include it. Trailing assistant
    messages whose results fall outside the window are dropped for the mirror
    reason.
    """
    if not window:
        return []

    start = len(full) - len(window)
    while start > 0:
        first = full[start]
        if not isinstance(first, ToolResultMessage):
            break
        start -= 1                        # pull in the assistant message above it

    kept = full[start:]

    result_ids = {m.tool_call_id for m in kept if isinstance(m, ToolResultMessage)}
    while kept and isinstance(kept[-1], AssistantMessage) and kept[-1].tool_calls:
        if all(c.id in result_ids for c in kept[-1].tool_calls):
            break
        kept = kept[:-1]
    return kept


def _shrink(message: Message, limit_tokens: int) -> Message:
    if not isinstance(message, ToolResultMessage):
        return message
    budget = max(500, int(limit_tokens * CHARS_PER_TOKEN / 10))
    if len(message.content) <= budget:
        return message
    body, _ = truncate_text(message.content, budget)
    return ToolResultMessage(tool_call_id=message.tool_call_id, content=body,
                             is_error=message.is_error)
