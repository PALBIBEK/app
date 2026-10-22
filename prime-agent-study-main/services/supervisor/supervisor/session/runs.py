"""A run's lifecycle, read from the journal: what happened to one message.

The shape follows OpenAI's background responses and xAI's deferred completions: a message is
accepted with a run id at once; the run is ``queued`` (waiting behind another), ``in_progress``,
``requires_action`` (waiting for an approval), then ends ``completed``, ``failed``,
``cancelled`` or ``incomplete`` (stopped by a limit). Anyone holding the run id can fetch the
run (its final answer, usage and error) without keeping a stream open.

Nothing is stored for this beyond what the session already keeps: the command row (the message,
its run id and when it was sent) and the run's journal events.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final

from supervisor.agent.events import (
    ApprovalRequired,
    ApprovalResolved,
    Envelope,
    ErrorEvent,
    MessageEnd,
    RunEnd,
    RunStart,
    ToolEnd,
    TurnEnd,
    TurnStart,
)

TERMINAL: Final = frozenset({"completed", "failed", "cancelled", "incomplete"})

_ENDED: Final = {
    "stop": "completed",
    "ok": "completed",
    "deferred": "completed",
    "aborted": "cancelled",
    "cancelled": "cancelled",
    "budget_exceeded": "incomplete",
    "error": "failed",
    "interrupted": "failed",
}

_LIMITS: Final = frozenset({"AgentLoopError", "BudgetExceeded", "BudgetCostExceeded", "BudgetTokensExceeded"})
"""Errors that are a limit reached (step limit, budget): the run is ``incomplete``, not broken."""


@dataclass(frozen=True)
class RunView:
    run_id: str
    command_id: str
    status: str
    created_at_ms: int
    started_at_ms: int | None = None
    ended_at_ms: int | None = None
    stop_reason: str | None = None
    output_text: str | None = None
    error: dict[str, str] | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    steps: int = 0
    tool_calls: int = 0
    pending_approval_id: str | None = None

    @property
    def done(self) -> bool:
        return self.status in TERMINAL


def _text(content: list[dict[str, Any]]) -> str:
    return "".join(str(b.get("text", "")) for b in content if isinstance(b, dict) and b.get("type") == "text")


def run_view(
    *, run_id: str, command_id: str, created_at_ms: int, command_status: str | None, events: list[Envelope]
) -> RunView:
    """What the journal says about run ``run_id`` (its events, in order)."""
    started = ended = None
    stop_reason: str | None = None
    output: str | None = None
    error: dict[str, str] | None = None
    usage: dict[str, Any] = {}
    summed = [0, 0, 0.0]  # input, output, cost over the run's model replies
    steps = tool_calls = 0
    waiting: dict[str, bool] = {}
    for env in events:
        p = env.payload
        if isinstance(p, RunStart):
            started = env.ts_ms
        elif isinstance(p, TurnStart):
            steps += 1
        elif isinstance(p, ToolEnd):
            tool_calls += 1
        elif isinstance(p, MessageEnd) and p.role == "assistant":
            if text := _text(p.content):
                output = text
            if p.usage is not None:
                summed[0] += p.usage.input_tokens
                summed[1] += p.usage.output_tokens
                summed[2] += p.usage.cost_total
        elif isinstance(p, ErrorEvent):
            error = {"class": p.error_class, "message": p.detail}
            if p.error_code:
                error["code"] = p.error_code
        elif isinstance(p, ApprovalRequired):
            waiting[p.approval_id] = True
        elif isinstance(p, ApprovalResolved):
            waiting.pop(p.approval_id, None)
        elif isinstance(p, TurnEnd):
            stop_reason = p.stop_reason
        elif isinstance(p, RunEnd):
            ended = env.ts_ms
            if p.total_usage is not None:
                u = p.total_usage
                usage = {
                    "input_tokens": u.input_tokens,
                    "output_tokens": u.output_tokens,
                    "total_tokens": u.input_tokens + u.output_tokens,
                    "cost": u.cost_total,
                }
    if not usage and (summed[0] or summed[1]):  # stepped runs report usage per model reply
        usage = {
            "input_tokens": summed[0],
            "output_tokens": summed[1],
            "total_tokens": summed[0] + summed[1],
            "cost": round(float(summed[2]), 6),
        }
    if ended is not None:
        status = _ENDED.get(stop_reason or "stop", "completed" if error is None else "failed")
        if status == "failed" and error is not None and error.get("class") in _LIMITS:
            status = "incomplete"
    elif started is not None:
        status = "requires_action" if waiting else "in_progress"
    else:
        status = "cancelled" if command_status == "cancelled" else "queued"
    return RunView(
        run_id=run_id,
        command_id=command_id,
        status=status,
        created_at_ms=created_at_ms,
        started_at_ms=started,
        ended_at_ms=ended,
        stop_reason=stop_reason,
        output_text=output,
        error=error,
        usage=usage,
        steps=steps,
        tool_calls=tool_calls,
        pending_approval_id=next(iter(waiting), None),
    )
