"""How a run's journal events read as its status (supervisor.session.runs)."""

from __future__ import annotations

from supervisor.agent.events import (
    ApprovalRequired,
    ApprovalResolved,
    Envelope,
    ErrorEvent,
    MessageEnd,
    RunEnd,
    RunStart,
    TurnEnd,
    TurnStart,
    Usage,
)
from supervisor.session.runs import run_view


def _env(seq: int, payload) -> Envelope:
    return Envelope("s", seq, f"e{seq}", 1000 + seq, "r1", None, payload)


def _view(*payloads, command_status=None):
    return run_view(run_id="r1", command_id="c1", created_at_ms=900, command_status=command_status,
                    events=[_env(i, p) for i, p in enumerate(payloads)])


def test_lifecycle() -> None:
    assert _view().status == "queued"
    assert _view(command_status="cancelled").status == "cancelled"
    assert _view(RunStart(), TurnStart()).status == "in_progress"
    waiting = _view(RunStart(), ApprovalRequired("a1", "ok?", ["allow", "deny"], 60))
    assert (waiting.status, waiting.pending_approval_id) == ("requires_action", "a1")
    assert _view(RunStart(), ApprovalRequired("a1", "ok?", [], 60), ApprovalResolved("a1", "allow", "responded")).status == "in_progress"


def test_endings() -> None:
    done = _view(RunStart(), TurnStart(), MessageEnd("assistant", [{"type": "text", "text": "hi"}]),
                 TurnEnd("stop"), RunEnd([], Usage(10, 5, cost_total=0.01)))
    assert (done.status, done.output_text, done.usage["total_tokens"], done.done) == ("completed", "hi", 15, True)
    assert _view(RunStart(), TurnEnd("aborted"), RunEnd([])).status == "cancelled"
    failed = _view(RunStart(), ErrorEvent("TurnInterrupted", False, "tool did not complete"), TurnEnd("interrupted"), RunEnd([]))
    assert failed.status == "failed" and failed.error == {"class": "TurnInterrupted", "message": "tool did not complete"}
    limit = _view(RunStart(), ErrorEvent("AgentLoopError", False, "step limit"), TurnEnd("error"), RunEnd([]))
    assert limit.status == "incomplete"
    assert _view(RunStart(), TurnEnd("budget_exceeded"), RunEnd([])).status == "incomplete"
