"""Non-LLM transcript entries and the conversion boundary (port of
`harness/messages.ts`).

The transcript records **what happened**; the context is **what the model
sees**. They are not the same list, and `convert_to_llm` is the single place
that turns one into the other.

Keeping them distinct is what lets a briefing, a shell command or a summary
be persisted with its own identity instead of being flattened into a
`UserMessage` — which would be replayed on resume as though the user had
typed it.

**Deviation from pi, deliberate:** pi switches on `role` inside one big
`convertToLlm`. Here each entry implements `to_llm()`. Same behaviour, but a
new entry type cannot be added while forgetting to teach the converter about
it — the switch's `default: undefined` silently drops anything unhandled,
which is a data-loss bug that looks like nothing at all.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from ..types import Message, UserMessage

COMPACTION_SUMMARY_PREFIX = (
    "The conversation history before this point was compacted into the "
    "following summary:\n\n<summary>\n"
)
COMPACTION_SUMMARY_SUFFIX = "\n</summary>"

BRANCH_SUMMARY_PREFIX = (
    "The following is a summary of a branch that this conversation came back "
    "from:\n\n<summary>\n"
)
BRANCH_SUMMARY_SUFFIX = "</summary>"


@dataclass
class CustomMessage(Message):
    """An app-defined transcript entry.

    Reaches the model as a user message **by default** — pi's `convertToLlm`
    converts `custom` unconditionally. `display` is a UI concern only;
    conflating it with model visibility would hide content from the model
    that the author meant to hide only from a terminal.
    """

    custom_type: str = ""
    content: str = ""
    display: bool = True
    details: Any = None
    timestamp: float = field(default_factory=time.time)

    def to_llm(self) -> Message | None:
        # `details` is deliberately not sent: it is structured data for logs
        # and UI, and dumping it into the prompt is how internal state leaks
        # into a conversation.
        return UserMessage(self.content)

    def to_wire(self) -> dict[str, Any]:
        raise TypeError(
            f"{type(self).__name__}({self.custom_type!r}) is a transcript entry, "
            "not a wire message; call convert_to_llm() first"
        )


@dataclass
class BashExecutionMessage(CustomMessage):
    """A shell command the *user* ran (pi's `!command`), not a tool call."""

    command: str = ""
    output: str = ""
    exit_code: int | None = None
    cancelled: bool = False
    truncated: bool = False
    full_output_path: str | None = None
    exclude_from_context: bool = False
    """pi's `!!command`: run it, show the human, spend no context on it.
    The only opt-out in the whole conversion."""

    def __post_init__(self) -> None:
        self.custom_type = self.custom_type or "bashExecution"

    def to_llm(self) -> Message | None:
        if self.exclude_from_context:
            return None
        return UserMessage(bash_execution_to_text(self))


@dataclass
class BranchSummaryMessage(CustomMessage):
    summary: str = ""
    from_id: str = ""

    def __post_init__(self) -> None:
        self.custom_type = self.custom_type or "branchSummary"

    def to_llm(self) -> Message | None:
        return UserMessage(BRANCH_SUMMARY_PREFIX + self.summary + BRANCH_SUMMARY_SUFFIX)


@dataclass
class CompactionSummaryMessage(CustomMessage):
    summary: str = ""
    tokens_before: int = 0

    def __post_init__(self) -> None:
        self.custom_type = self.custom_type or "compactionSummary"

    def to_llm(self) -> Message | None:
        return UserMessage(COMPACTION_SUMMARY_PREFIX + self.summary
                           + COMPACTION_SUMMARY_SUFFIX)


def bash_execution_to_text(message: BashExecutionMessage) -> str:
    """Render a shell execution for the model.

    Fenced so the model can tell output from narration, and explicit about
    "(no output)" — a blank rendering reads as a failed command and invites
    a pointless re-run.
    """
    text = f"Ran `{message.command}`\n"
    text += f"```\n{message.output}\n```" if message.output else "(no output)"

    if message.cancelled:
        text += "\n\n(command cancelled)"
    elif message.exit_code not in (None, 0):
        text += f"\n\nCommand exited with code {message.exit_code}"

    if message.truncated and message.full_output_path:
        text += f"\n\n[Output truncated. Full output: {message.full_output_path}]"
    return text


def create_custom_message(custom_type: str, content: str, display: bool = True,
                          details: Any = None) -> CustomMessage:
    return CustomMessage(custom_type=custom_type, content=content,
                         display=display, details=details)


def create_branch_summary(summary: str, from_id: str) -> BranchSummaryMessage:
    return BranchSummaryMessage(summary=summary, from_id=from_id)


def create_compaction_summary(summary: str, tokens_before: int) -> CompactionSummaryMessage:
    return CompactionSummaryMessage(summary=summary, tokens_before=tokens_before)


def convert_to_llm(messages: list[Message]) -> list[Message]:
    """Transcript → what the provider receives.

    Ordinary messages pass through untouched; custom entries delegate to
    their own `to_llm()`, and a `None` means "not part of the context".
    Returns a new list — the caller's transcript is never consumed.
    """
    out: list[Message] = []
    for message in messages:
        if isinstance(message, CustomMessage):
            converted = message.to_llm()
            if converted is not None:
                out.append(converted)
            continue
        out.append(message)
    return out
