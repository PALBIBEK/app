"""How a subagent's run is read and graded, and which calls may start one (githi's rules)."""

from __future__ import annotations

import pytest

from supervisor.agent.events import Envelope, ErrorEvent, MessageEnd, RunStart, ToolEnd, TurnEnd
from supervisor.session.children import ChildOutcome, graded, last_outcome, status_of
from supervisor.temporal.subagent_tools import DepthExceeded, delegated, task
from supervisor.tools import InvalidArguments


def _env(seq: int, payload: object) -> Envelope:
    return Envelope(event_id=f"e{seq}", session_id="c", seq=seq, ts_ms=0, turn_id="r", trace_id=None, payload=payload)  # type: ignore[arg-type]


def _said(text: str) -> MessageEnd:
    return MessageEnd(role="assistant", content=[{"type": "text", "text": text}])


def test_the_last_runs_outcome_is_read() -> None:
    events = [
        _env(0, RunStart()),
        _env(1, _said("first run's answer")),
        _env(2, TurnEnd(stop_reason="stop")),
        _env(3, RunStart()),
        _env(4, _said("halfway there")),
        _env(5, ToolEnd(tool_call_id="c", tool_name="ls", result={})),
        _env(6, ErrorEvent(error_class="AgentLoopError", retryable=False, detail="the step limit")),
        _env(7, TurnEnd(stop_reason="error")),
    ]
    assert last_outcome(events) == ChildOutcome("error", "halfway there", "AgentLoopError", "the step limit")
    assert last_outcome(events[:5]) is None, "a run that has not ended has no outcome"


@pytest.mark.parametrize(
    ("outcome", "status"),
    [
        (ChildOutcome("stop", "an answer"), "completed"),
        (ChildOutcome("stop", ""), "failed"),
        (ChildOutcome("aborted", "half"), "cancelled"),
        (ChildOutcome("error", "", "ModelError"), "failed"),
    ],
)
def test_a_childs_job_status(outcome: ChildOutcome, status: str) -> None:
    assert status_of(outcome) == status


@pytest.mark.parametrize(
    ("outcome", "ok", "says"),
    [
        (ChildOutcome("stop", "the answer"), True, "the answer"),
        (ChildOutcome("stop", "  "), False, "without producing a final answer"),
        (ChildOutcome("aborted", "partial"), False, "stopped before it finished\nPartial output"),
        (ChildOutcome("error", "", "RunTimeLimit", "1 s"), False, "ran out of room before it finished (1 s)"),
        (ChildOutcome("budget_exceeded"), False, "budget is spent"),
        (ChildOutcome("error", "", "ModelError", "boom"), False, "run failed (boom)"),
    ],
)
def test_only_an_answer_is_a_success(outcome: ChildOutcome, ok: bool, says: str) -> None:
    graded_ok, text = graded(outcome)
    assert graded_ok is ok and says in text


def test_which_calls_start_a_child() -> None:
    assert delegated("subagent", {"prompt": "x"}) == ("spawn", False)
    assert delegated("subagent_fork", {"prompt": "x", "run_in_background": True}) == ("fork", True)
    assert delegated("ls", {}) is None


def test_a_task_needs_a_prompt_and_room_below_the_depth_cap() -> None:
    ok = task("subagent", {"prompt": " do it ", "description": "it"}, depth=0, max_depth=1, background_allowed=True)
    assert ok.prompt == "do it" and ok.label == "it" and not ok.background
    with pytest.raises(InvalidArguments):
        task("subagent", {"prompt": "  "}, depth=0, max_depth=3, background_allowed=True)
    with pytest.raises(DepthExceeded, match="depth 2 but max_depth is 1"):
        task("subagent_fork", {"prompt": "x"}, depth=1, max_depth=1, background_allowed=True)
    with pytest.raises(InvalidArguments, match="may not start background"):
        task("subagent", {"prompt": "x", "run_in_background": True}, depth=0, max_depth=3, background_allowed=False)
