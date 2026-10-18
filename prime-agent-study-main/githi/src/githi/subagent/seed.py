"""Fork seed: completed parent turns only (DeepSeek fork provider).

DeepSeek cuts the parent log at the last ``turn/end``. githi has no turn
events, so we cut before the open assistant tool-call batch (unanswered
``tool_calls``), which always includes the in-flight ``subagent_fork`` call,
and also drop the user message(s) that opened that turn.
"""

from __future__ import annotations

import copy
from typing import Sequence

from ..types import AssistantMessage, Message, ToolResultMessage, UserMessage


def _answered_ids_after(messages: Sequence[Message], start: int) -> set[str]:
    ids: set[str] = set()
    for msg in messages[start:]:
        if isinstance(msg, ToolResultMessage):
            ids.add(msg.tool_call_id)
    return ids


def open_tool_turn_index(messages: Sequence[Message]) -> int | None:
    """Index of the last assistant whose tool_calls are not all answered.

    Returns None when every tool batch in the transcript is complete.
    """
    open_at: int | None = None
    for i, msg in enumerate(messages):
        if not isinstance(msg, AssistantMessage) or not msg.tool_calls:
            continue
        needed = {c.id for c in msg.tool_calls}
        answered = _answered_ids_after(messages, i + 1)
        if not needed.issubset(answered):
            open_at = i
    return open_at


def completed_turn_prefix(messages: Sequence[Message]) -> list[Message]:
    """Deep-copied messages from completed turns only.

    Excludes the in-flight assistant, anything after it, and the user
    message(s) that started that open turn. Empty (or system-only) when the
    parent has no completed turns yet — close to a fresh spawn seed.
    """
    cut = open_tool_turn_index(messages)
    if cut is None:
        end = len(messages)
    else:
        end = cut
        while end > 0 and isinstance(messages[end - 1], UserMessage):
            end -= 1
    return copy.deepcopy(list(messages[:end]))
