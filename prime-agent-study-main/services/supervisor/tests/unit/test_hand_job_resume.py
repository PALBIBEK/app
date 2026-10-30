"""A retried background job reads back the calls it made from the session's journal."""

from __future__ import annotations

from supervisor.agent.events import ToolStart
from supervisor.temporal.hand_jobs import JobCall, calls_made

JOB = "job-0123456789abcdef"


def _start(call: JobCall) -> ToolStart:
    return ToolStart(tool_call_id=call.op_id, tool_name=call.name, args=call.arguments)


def test_a_calls_id_names_what_it_asks_so_another_call_never_gets_its_result() -> None:
    write = JobCall.asked(JOB, 0, 0, "write", {"path": "a.txt", "content": "x"})
    again = JobCall.asked(JOB, 0, 0, "write", {"path": "a.txt", "content": "x"})
    other = JobCall.asked(JOB, 0, 0, "bash", {"command": "rm -rf build"})
    assert write.op_id == again.op_id, "the same call at the same place is the same operation"
    assert write.op_id != other.op_id
    assert write.op_id.startswith(f"{JOB}:0:0:")


def test_the_calls_made_come_back_step_by_step_in_order() -> None:
    a = JobCall.asked(JOB, 0, 0, "read", {"path": "a"})
    b = JobCall.asked(JOB, 0, 1, "read", {"path": "b"})
    c = JobCall.asked(JOB, 1, 0, "write", {"path": "c", "content": "c"})
    journalled = [_start(c), _start(b), _start(a)]  # in any order
    assert calls_made(JOB, journalled) == [[a, b], [c]]


def test_a_step_cut_off_part_way_holds_the_calls_that_started() -> None:
    a = JobCall.asked(JOB, 0, 0, "read", {"path": "a"})
    assert calls_made(JOB, [_start(a)]) == [[a]]


def test_other_calls_and_steps_past_a_gap_are_not_the_jobs_record() -> None:
    mine = JobCall.asked(JOB, 0, 0, "read", {"path": "a"})
    later = JobCall.asked(JOB, 2, 0, "read", {"path": "c"})  # step 1 never journalled
    theirs = JobCall.asked("job-ffffffffffffffff", 0, 0, "read", {"path": "a"})
    session = ToolStart(tool_call_id="call-0-0", tool_name="read", args={"path": "a"})
    assert calls_made(JOB, [_start(mine), _start(later), _start(theirs), session]) == [[mine]]


def test_calls_journalled_before_ids_named_what_they_ask_resume_too() -> None:
    old = ToolStart(tool_call_id=f"{JOB}:0:0", tool_name="write", args={"path": "a"})
    assert calls_made(JOB, [old]) == [[JobCall(f"{JOB}:0:0", "write", {"path": "a"})]]
