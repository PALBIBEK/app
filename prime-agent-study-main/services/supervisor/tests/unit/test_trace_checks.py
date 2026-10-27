"""Automatic checks on a finished run: each passes or fails with a reason, and only applies
when the run gives it something to judge."""

from __future__ import annotations

from typing import Any

from supervisor.agent.events import (
    ApprovalRequired,
    ApprovalResolved,
    ErrorEvent,
    RunEnd,
    RunStart,
    ToolEnd,
    ToolStart,
    TurnEnd,
    TurnStart,
)
from supervisor.traces.checks import Check, run_checks
from tests.unit.test_traces_build import Journal, model, ran


def checks(j: Journal) -> dict[str, Check]:
    return {c.name: c for c in run_checks(j.trace())}


def tool(j: Journal, at: int, cid: str, name: str, args: dict[str, Any], result: ToolEnd) -> Journal:
    return j.at(at, ToolStart(cid, name, args)).at(at + 5, result)


def test_an_answered_run_with_no_tools_is_only_checked_for_finishing() -> None:
    found = checks(Journal().at(0, RunStart()).at(0, TurnStart()).at(10, model("hi")).at(20, TurnEnd("stop"))
                   .at(20, RunEnd(messages=[])))
    assert list(found) == ["finished"] and found["finished"].passed


def test_a_run_still_going_is_not_judged() -> None:
    assert run_checks(Journal().at(0, RunStart()).at(0, TurnStart()).trace()) == []


def test_a_failed_run_and_a_failed_tool_fail_their_checks_and_point_at_the_span() -> None:
    j = Journal().at(0, RunStart()).at(0, TurnStart())
    tool(j, 10, "c1", "read", {"file_path": "a.py"}, ran("c1", "read", "x = 1\n"))
    tool(j, 20, "c2", "read", {"file_path": "gone.py"}, ran("c2", "read", ok=False, code="NOT_FOUND", error="no such file"))
    j.at(30, ErrorEvent("GatewayError", False, "model unavailable")).at(40, TurnEnd("error")).at(40, RunEnd(messages=[]))
    trace = j.trace()
    found = {c.name: c for c in run_checks(trace)}
    assert not found["finished"].passed and found["finished"].reason == "GatewayError: model unavailable"
    failing = found["tool_errors"]
    assert not failing.passed
    assert failing.reason == "1 of 2 tool calls failed; first: read gone.py (no such file)"
    assert failing.span_id == next(s.id for s in trace.spans if s.attrs.get("call_id") == "c2")


def test_the_same_call_three_times_reads_as_a_loop() -> None:
    j = Journal().at(0, RunStart()).at(0, TurnStart())
    for i in range(3):
        tool(j, 10 + i * 10, f"c{i}", "bash", {"command": "make"}, ran(f"c{i}", "bash", {"exit_code": 2, "stdout": ""}))
    tool(j, 50, "c9", "bash", {"command": "make clean"}, ran("c9", "bash", {"exit_code": 0, "stdout": ""}))
    j.at(60, TurnEnd("stop")).at(60, RunEnd(messages=[]))
    loop = checks(j)["repeated_calls"]
    assert not loop.passed and loop.reason == "ran $ make 3 times with the same arguments"


def test_approvals_fail_when_any_was_denied() -> None:
    j = Journal().at(0, RunStart()).at(0, TurnStart())
    for i, (choice, status) in enumerate((("allow", "approved"), ("deny", "timed_out"))):
        meta = {"tool": "bash", "tool_call_id": f"c{i}"}
        j.at(i * 100, ApprovalRequired(f"a{i}", "Allow?", ["allow", "deny"], 60, metadata=meta))
        j.at(i * 100 + 50, ApprovalResolved(f"a{i}", choice, status))
    j.at(300, TurnEnd("stop")).at(300, RunEnd(messages=[]))
    found = checks(j)["approvals"]
    assert not found.passed and found.reason == "1 of 2 denied (1 unanswered)"


def test_tests_after_the_last_change_are_judged_by_their_exit_code() -> None:
    def run(last_exit: int) -> Check:
        j = Journal().at(0, RunStart()).at(0, TurnStart())
        tool(j, 10, "t0", "bash", {"command": "pytest -q"}, ran("t0", "bash", {"exit_code": 1, "stdout": ""}))
        tool(j, 20, "e1", "edit", {"file_path": "a.py", "old_text": "a", "new_text": "b"}, ran("e1", "edit", {}))
        tool(j, 30, "t1", "bash", {"command": "python -m pytest -q tests"},
             ran("t1", "bash", {"exit_code": last_exit, "stdout": ""}))
        j.at(40, TurnEnd("stop")).at(40, RunEnd(messages=[]))
        return checks(j)["tests_after_change"]

    assert run(0).passed and run(0).reason == "$ python -m pytest -q tests passed after the last change"
    assert not run(1).passed and run(1).reason == "$ python -m pytest -q tests exited 1 after the last change"

    j = Journal().at(0, RunStart()).at(0, TurnStart())
    tool(j, 20, "e1", "write", {"file_path": "a.py", "content": "x"}, ran("e1", "write", {}))
    j.at(40, TurnEnd("stop")).at(40, RunEnd(messages=[]))
    assert "tests_after_change" not in checks(j)  # changed, never tested: no verdict


def test_a_run_a_person_stopped_is_not_held_against_the_agent() -> None:
    j = (Journal().at(0, RunStart()).at(0, TurnStart()).at(10, model(calls=[("c1", "bash", {"command": "sleep 99"})]))
         .at(20, TurnEnd("aborted")).at(20, RunEnd(messages=[])))
    assert "finished" not in checks(j)
