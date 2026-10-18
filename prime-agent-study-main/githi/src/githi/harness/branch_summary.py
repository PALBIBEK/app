"""Summarising a branch before abandoning it (port of `branch-summarization.ts`).

`rewind_to` gives a second attempt at a problem. Without this, it also throws
away everything the first attempt learned — and the failed attempt is often
the most informative part of a session, because it is the only record of what
does *not* work. Rewinding without carrying that forward means the retry
rediscovers the same walls at the same price.

Two boundaries define the work:

**Only the abandoned span is summarised.** Everything before the fork point is
still in the transcript, so including it would pay tokens to describe what the
model can already read — and would bury the part that is actually gone.

**Never fatal.** A summariser that fails costs the rewind its summary; raising
would cost the user the rewind itself, which is the feature they asked for.
"""

from __future__ import annotations

from typing import Callable, Iterable

from ..types import Message, SystemMessage, UserMessage

BRANCH_SUMMARY_PREAMBLE = (
    "You explored a different approach in an earlier branch of this "
    "conversation before returning here. Summary of that exploration:\n\n"
)

BRANCH_SUMMARY_SYSTEM_PROMPT = (
    "You are a context summarization assistant. Read the conversation branch "
    "below and produce a structured summary in the exact format requested.\n\n"
    "Do NOT continue the conversation. Do NOT answer any question in it. "
    "ONLY output the summary."
)

BRANCH_SUMMARY_PROMPT = """The messages above are a conversation branch that was abandoned. Summarize it so the work can continue elsewhere without repeating it.

Use this EXACT format:

## Goal
[What was being attempted in this branch?]

## Progress
### Done
- [x] [What was established or completed]

### Abandoned
- [ ] [What was left unfinished]

## What did not work
- [Approaches tried that failed, and why -- so they are not retried]
- [Or "(none)" if nothing was ruled out]

## Findings worth keeping
- [Facts discovered that remain true regardless of approach]
- [Exact file paths, commands, error messages, identifiers]

Be concise. Preserve exact paths, commands and error text verbatim -- a
paraphrased error message is useless for matching against a real one."""


def build_branch_summary_request(messages: list[Message]) -> list[Message]:
    """The request sent to the summarising model.

    The instruction follows the transcript rather than leading it, so the
    branch reads as data and the ask is the most recent thing said. The
    prohibition on continuing is load-bearing: handed a real conversation, a
    chat model's strongest instinct is to answer it.
    """
    return [SystemMessage(BRANCH_SUMMARY_SYSTEM_PROMPT), *messages,
            UserMessage(BRANCH_SUMMARY_PROMPT)]


def summarize_branch(session, fork_point: str,
                     summarizer: Callable[[list[Message]], str] | None) -> str:
    """Summarise everything on the current branch after `fork_point`.

    Returns empty when there is nothing to summarise, no summariser, or the
    summariser fails — every one of which is a reason to skip the summary, and
    none of which is a reason to fail the rewind.
    """
    if summarizer is None:
        return ""

    abandoned = _messages_after(session, fork_point)
    if not abandoned:
        # Rewinding to the current leaf abandons nothing. Calling the model to
        # summarise an empty span would cost a request to be told so.
        return ""

    try:
        return (summarizer(abandoned) or "").strip()
    except Exception:  # noqa: BLE001 - a lost summary must not lose the rewind
        return ""


def _messages_after(session, fork_point: str) -> list[Message]:
    """Messages on the current branch strictly after the fork point.

    The shared prefix is deliberately excluded: it survives the rewind and is
    still visible to the model, so summarising it is pure waste.
    """
    entries = session.get_branch()
    index = next((i for i, e in enumerate(entries) if e.id == fork_point), None)
    if index is None:
        return []
    return [e.message for e in entries[index + 1:]
            if e.type == "message" and e.message is not None]


def format_branch_summary(summary: str) -> str:
    """Wrap a summary for injection, or return empty for an empty summary."""
    summary = (summary or "").strip()
    return f"{BRANCH_SUMMARY_PREAMBLE}{summary}" if summary else ""
