"""Subagents are sessions: a parent's tool call starts a child session that runs as its own agent.

The child pins what its parent pinned (harness, composition, model, tools) and has its own
conversation, steps and journal, so its work can be read as any session's can. It works in
the sandbox of the session at the top of its tree (its *root*), as githi's child works in its
parent's directory: what it writes the parent reads. Its spend counts toward the root's cost
cap, so a tree of subagents shares one budget, and it stops and ends with its parent.

githi (``githi/src/githi/subagent``) is the reference for how they behave:

* ``subagent`` starts a fresh child: it sees only its task, never the parent's conversation.
* ``subagent_fork`` starts a child that carries the parent's completed turns (not the turn
  that asks for it), then its task.
* A child cannot use the parent's control tools or ask the user; it may start subagents of its
  own while it is shallower than the harness's ``subagents.max_depth``.
* The parent waits for the child's last run and reads its outcome, graded as githi grades it
  (``graded``): only an answer is a success; a stop, a limit or an error is a failure that
  carries whatever partial answer the child gave.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from supervisor.agent.events import Envelope, ErrorEvent, MessageEnd, RunStart, TurnEnd

__all__ = [
    "LIMIT_ERRORS",
    "PARENT_ONLY_TOOLS",
    "SUBAGENT_BRIEF",
    "ChildOutcome",
    "graded",
    "last_outcome",
    "status_of",
]

PARENT_ONLY_TOOLS: Final = frozenset(
    {"send_message", "interrupt_agent", "list_agents", "job_output", "job_kill", "subagent_continuable"}
)
"""The parent's control tools (githi's ``PARENT_ONLY_TOOLS``): never offered to a child."""

SUBAGENT_BRIEF: Final = (
    "You are a subagent: another agent gave you one task. Do it with your tools, then answer "
    "with everything that agent needs: your final answer is all it sees of your work. You "
    "cannot ask the user anything; when a decision is not yours to make, say so in your answer. "
    "You work in the same files as the agent that started you."
)
"""Leads a child's system prompt, after its pinned prompt."""

LIMIT_ERRORS: Final = frozenset(
    {"AgentLoopError", "RunTimeLimit", "RunTokenLimit", "RunDeadline", "SessionTimeLimit", "SessionTokenLimit"}
)
"""A run that ended on one of these ran out of room (githi's MAX_TURNS), it did not fail."""


@dataclass(frozen=True, slots=True)
class ChildOutcome:
    """How a child's run ended: its stop reason, the error that ended it, and its last text."""

    stop_reason: str
    text: str = ""
    error_class: str | None = None
    detail: str = ""


def last_outcome(events: Sequence[Envelope]) -> ChildOutcome | None:
    """The outcome of the last run in ``events`` (a child's journal); None before one ended."""
    start = max((i for i, e in enumerate(events) if isinstance(e.payload, RunStart)), default=None)
    if start is None:
        return None
    stop: str | None = None
    text = ""
    error: ErrorEvent | None = None
    for envelope in events[start:]:
        match envelope.payload:
            case MessageEnd(role="assistant", content=content):
                text = _text(content) or text
            case ErrorEvent() as e:
                error = e
            case TurnEnd(stop_reason=reason):
                stop = reason
    if stop is None:
        return None
    return ChildOutcome(
        stop_reason=stop,
        text=text,
        error_class=error.error_class if error else None,
        detail=error.detail if error else "",
    )


def status_of(outcome: ChildOutcome) -> str:
    """githi's job status for a child's run: completed (it answered), cancelled (it was
    stopped), or failed (anything else, an empty answer included)."""
    if outcome.stop_reason == "aborted":
        return "cancelled"
    return "completed" if graded(outcome)[0] else "failed"


def graded(outcome: ChildOutcome) -> tuple[bool, str]:
    """(success, what the parent reads), as githi's ``map_run_to_outcome`` grades a child."""
    text = outcome.text.strip()
    if outcome.stop_reason == "stop":
        if text:
            return True, text
        return False, "the subagent finished without producing a final answer"
    if outcome.stop_reason == "aborted":
        return False, _with_partial("the subagent's run was stopped before it finished", text)
    if outcome.stop_reason == "budget_exceeded":
        return False, _with_partial(
            "the subagent was stopped: the session's budget is spent" + _why(outcome.detail), text
        )
    if outcome.error_class in LIMIT_ERRORS:
        return False, _with_partial("the subagent ran out of room before it finished" + _why(outcome.detail), text)
    return False, _with_partial("the subagent's run failed" + _why(outcome.detail), text)


def _why(detail: str) -> str:
    return f" ({detail.strip()})" if detail.strip() else ""


def _with_partial(headline: str, text: str) -> str:
    return f"{headline}\nPartial output before the run ended:\n{text}" if text else headline


def _text(content: Sequence[object]) -> str:
    return "".join(
        str(part.get("text") or "") for part in content if isinstance(part, dict) and part.get("type") == "text"
    ).strip()
