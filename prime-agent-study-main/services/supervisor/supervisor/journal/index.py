"""What a session's index row (``SessionIndexRow``) says about it, from what it journals.

- ``message_count``: the messages a person reads in its chat: each one sent to it, and
  each reply of the agent's that has text in it (a step that only called tools shows as
  its tools). It counts up as they are journalled; it used to be the length of the
  conversation the model last held, set only when a run ended: it counted tool results,
  shrank when a compaction summarised, and jumped once a run was over.
- ``title``: the first line of the first message sent to it (credentials masked), until
  someone names it otherwise.
- ``last_event_at``: when it last did anything (a message, a model step, a run's end):
  what idle reaping and retention go by.
"""

from __future__ import annotations

from typing import Any, Final

from supervisor.agent.events import MessageEnd, RunEnd
from supervisor.journal.secrets import mask_text

__all__ = ["TITLE_MAX", "active", "in_chat", "title_of"]

TITLE_MAX: Final = 80


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(p.get("text") or "") for p in content if isinstance(p, dict) and p.get("type") == "text")
    return ""


def in_chat(event: object) -> bool:
    """Whether ``event`` is a message a person reads in the session's chat."""
    if not isinstance(event, MessageEnd):
        return False
    return event.role == "user" or (event.role == "assistant" and bool(_text(event.content).strip()))


def title_of(event: object) -> str | None:
    """The title a session's first message gives it, if ``event`` is one sent to it."""
    if not isinstance(event, MessageEnd) or event.role != "user":
        return None
    line = next((ln.strip() for ln in _text(event.content).splitlines() if ln.strip()), "")
    if not line:
        return None
    line = mask_text(line)
    return line if len(line) <= TITLE_MAX else line[: TITLE_MAX - 1].rstrip() + "…"


def active(event: object) -> bool:
    """Whether ``event`` is the session doing something (its ``last_event_at``)."""
    return isinstance(event, MessageEnd | RunEnd)
