"""Subagents are sessions, as githi's are agents of their own (``supervisor.session.children``).

A ``subagent`` call starts a child session that runs its own loop with its parent's tools,
working in its parent's files; the parent reads the child's answer. A fork carries the
parent's completed turns. A failed child is graded as githi grades it, a stop reaches the
child too, and the child's spend counts toward its parent's.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from temporalio.client import WorkflowExecutionStatus
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Replayer

from supervisor.agent.events import (
    MessageEnd,
    SubagentEnd,
    SubagentNotice,
    SubagentStart,
    ToolEnd,
    TurnEnd,
)
from supervisor.app import SupervisorState
from supervisor.db.models import SessionIndexRow, SessionRow
from supervisor.hands.keys import hand_key
from supervisor.main import create_app
from supervisor.seams.providers.storage.memory_object_storage import MemoryObjectStorage
from supervisor.seams.providers.workspace.memory_workspace_store import MemoryWorkspaceStore
from supervisor.session.children import SUBAGENT_BRIEF
from supervisor.temporal import steps
from supervisor.temporal.contracts import Message, workflow_id
from supervisor.temporal.job_workflow import SubagentJobWorkflow
from supervisor.temporal.worker import session_worker
from supervisor.temporal.workflow import AgentSessionWorkflow
from supervisor.traces.kinds import SpanKind
from supervisor.traces.store import TraceFilter, list_runs, load_run
from tests.integration.test_many_hands import create_with_hands
from tests.support.githi_env import LS, WRITE, Env, event_types, has_event, payloads, run_ended
from tests.support.scripted import ScriptedGateway, slow_tool

pytestmark = [pytest.mark.asyncio(loop_scope="session"), pytest.mark.usefixtures("memory_service_url")]

READ = ("read", {"path": "notes.txt"})
HEADERS = {"X-Tenant-Id": "t1"}


@asynccontextmanager
async def api(env: Env) -> AsyncIterator[AsyncClient]:
    state = await env.state()
    state.workflows = env.sessions
    async with AsyncClient(transport=ASGITransport(app=create_app(state=state)), base_url="http://test") as http:
        yield http


def _subagent_end(events: list) -> ToolEnd:
    (end,) = [e for e in payloads(events, ToolEnd) if e.tool_name in ("subagent", "subagent_fork")]
    return end


async def _child_row(env: Env, parent: str) -> SessionRow:
    async with env.factory() as db:  # type: ignore[operator]
        (row,) = (await db.scalars(select(SessionRow).where(SessionRow.parent_session_id == parent))).all()
        return row


async def test_a_subagent_does_the_task_in_its_parents_files_and_answers(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    gateway = model(
        ("subagent", {"prompt": "write the notes file", "description": "notes"}),
        subagent_calls=[WRITE],
        subagent_answer="wrote notes.txt",
    )
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state)
        await env.sessions.submit(sid, Message("c1", "get a subagent to write the notes"))
        events = await env.wait_for(state, sid, run_ended())
        child = await _child_row(env, sid)
        gateway.calls = [READ]  # the parent reads what its child wrote
        await env.sessions.submit(sid, Message("c2", "read them"))
        events = await env.wait_for(state, sid, run_ended(2))
        child_events = await state.reader.read(child.id)
        handle = env.client.get_workflow_handle(workflow_id(child.id))
        await asyncio.wait_for(handle.result(), timeout=30)  # its session ended with its task
        assert (await handle.describe()).status is WorkflowExecutionStatus.COMPLETED

    end = _subagent_end(events)
    assert not end.is_error and end.result["payload"]["text"] == "wrote notes.txt"
    assert end.result["payload"]["subagent_id"] == child.id
    (start,) = payloads(events, SubagentStart)
    assert (start.child_session_id, start.mode, start.label) == (child.id, "spawn", "notes")
    assert payloads(events, SubagentEnd)[0].output == "wrote notes.txt"
    read = next(e for e in payloads(events, ToolEnd) if e.tool_name == "read")
    assert read.result["payload"] == "hello", "the parent reads the file its child wrote"
    # The child is a session of its own: its task, its own tool call, its own answer.
    assert (child.root_session_id, child.subagent_depth, child.subagent_mode) == (sid, 1, "spawn")
    users = [m for m in payloads(child_events, MessageEnd) if m.role == "user"]
    assert users[0].content[0]["text"] == "write the notes file"
    assert payloads(child_events, MessageEnd)[0].role == "user", "a spawn starts empty"
    assert [e.tool_name for e in payloads(child_events, ToolEnd)] == ["write"]
    assert payloads(child_events, TurnEnd)[-1].stop_reason == "stop"
    # It never saw the parent's conversation, and was told it is a subagent.
    (first,) = gateway.subagent_seen[:1]
    assert any(m.content == SUBAGENT_BRIEF for m in first)
    assert not any("get a subagent" in m.content for m in first)


async def test_a_fork_carries_the_completed_turns_only(env: Env, model: Callable[..., ScriptedGateway]) -> None:
    gateway = model(subagent_answer="BANANA")
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state)
        await env.sessions.submit(sid, Message("c1", "the code word is BANANA"))
        await env.wait_for(state, sid, run_ended())
        gateway.calls = [("subagent_fork", {"prompt": "say the code word"})]
        await env.sessions.submit(sid, Message("c2", "ask a fork for the code word"))
        events = await env.wait_for(state, sid, run_ended(2))
        child = await _child_row(env, sid)

    assert child.subagent_mode == "fork"
    users = [m.content for m in gateway.subagent_seen[0] if m.role == "user"]
    assert users == ["the code word is BANANA", "say the code word"], "completed turns, then its task"
    assert _subagent_end(events).result["payload"]["text"] == "BANANA"


async def test_a_child_that_runs_out_of_room_fails_with_what_it_had(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    state = await env.state()
    # One call a step (a two-step run's requests end with a steps-left notice).
    model(("subagent", {"prompt": "list twice"}), subagent_calls=[LS, LS], one_at_a_time=True)
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state, "githi-short")  # the child asks twice and hits the limit
        await env.sessions.submit(sid, Message("c1", "delegate"))
        events = await env.wait_for(state, sid, has_event(lambda p: isinstance(p, SubagentEnd)))

    end = _subagent_end(events)
    assert end.is_error and end.result["code"] == "SUBAGENT_FAILED"
    assert "ran out of room" in end.result["error"]


async def test_stopping_the_parent_stops_its_subagent(env: Env, model: Callable[..., ScriptedGateway]) -> None:
    model(("subagent", {"prompt": "list slowly"}), subagent_calls=[LS])
    state = await env.state()
    with slow_tool("ls", 4.0):
        async with session_worker(env.client, state, task_queue=env.queue):
            sid = await env.create(state)
            await env.sessions.submit(sid, Message("c1", "delegate"))
            await env.wait_for(state, sid, has_event(lambda p: isinstance(p, SubagentStart)))
            child = await _child_row(env, sid)
            await env.wait_for(state, child.id, has_event(lambda p: getattr(p, "tool_name", None) == "ls"))
            await env.sessions.stop_run(sid, "c1")
            events = await env.wait_for(state, sid, run_ended(), timeout=60)
            child_events = await env.wait_for(state, child.id, run_ended(), timeout=60)

    assert payloads(events, TurnEnd)[-1].stop_reason == "aborted"
    assert payloads(child_events, TurnEnd)[-1].stop_reason == "aborted", "the stop reached the child"
    end = _subagent_end(events)
    assert end.is_error and "stopped before it finished" in end.result["error"]


async def test_a_rounds_subagents_work_at_once_and_their_spend_is_the_parents(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    model(
        ("subagent", {"prompt": "first task"}),
        ("subagent", {"prompt": "second task"}),
        cost_per_call=0.01,
        subagent_calls=[LS],
    )
    state = await env.state()
    with slow_tool("ls", 1.5):
        async with session_worker(env.client, state, task_queue=env.queue):
            sid = await env.create(state)
            await env.sessions.submit(sid, Message("c1", "two at once"))
            events = await env.wait_for(state, sid, run_ended())

    ends = [e for e in payloads(events, ToolEnd) if e.tool_name == "subagent"]
    assert len(ends) == 2 and not any(e.is_error for e in ends)
    async with env.factory() as db:  # type: ignore[operator]
        children = (await db.scalars(select(SessionRow.id).where(SessionRow.parent_session_id == sid))).all()
        costs = {
            r.session_id: Decimal(str(r.total_cost))
            for r in await db.scalars(select(SessionIndexRow).where(SessionIndexRow.session_id.in_([sid, *children])))
        }
    starts = [e.seq for e in events if isinstance(e.payload, SubagentStart)]
    first_end = min(e.seq for e in events if isinstance(e.payload, SubagentEnd))
    assert len(starts) == 2 and max(starts) < first_end, "the second started before the first ended"
    own = Decimal("0.02")  # the parent's two model calls
    assert costs[sid] == own + sum(costs[c] for c in children), "a child's spend is its parent's too"
    assert all(costs[c] == Decimal("0.02") for c in children)


async def test_subagent_histories_replay_deterministically(env: Env, model: Callable[..., ScriptedGateway]) -> None:
    """A parent and its subagents (started, waited on, answered, ended) replay as recorded."""
    model(("subagent", {"prompt": "list", "description": "lister"}), subagent_calls=[LS])
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state)
        await env.sessions.submit(sid, Message("c1", "delegate"))
        await env.wait_for(state, sid, run_ended())
        child = await _child_row(env, sid)
        await asyncio.wait_for(env.client.get_workflow_handle(workflow_id(child.id)).result(), timeout=30)
        await env.sessions.terminate(sid)

    replayer = Replayer(workflows=[AgentSessionWorkflow, SubagentJobWorkflow], data_converter=pydantic_data_converter)
    for session in (sid, child.id):
        history = await env.client.get_workflow_handle(workflow_id(session)).fetch_history()
        result = await replayer.replay_workflow(history, raise_on_replay_failure=False)
        assert result.replay_failure is None, (session, result.replay_failure)


async def test_a_subagents_run_is_in_its_parents_trace_and_nowhere_else(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    model(("subagent", {"prompt": "list", "description": "lister"}), subagent_calls=[LS], cost_per_call=0.01)
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue), api(env) as http:
        sid = await env.create(state)
        await env.sessions.submit(sid, Message("c1", "delegate"))
        events = await env.wait_for(state, sid, run_ended())
        listed = (await http.get("/v1/sessions", headers=HEADERS)).json()
    child = await _child_row(env, sid)
    row = await state.manager._get_session_row(sid)
    trace = await load_run(env.factory, state.reader, row, events[-1].turn_id)  # type: ignore[arg-type]
    assert trace is not None

    call = next(s for s in trace.spans if s.kind is SpanKind.TOOL and s.name == "subagent")
    (stand_in,) = [s for s in trace.spans if s.parent_id == call.id]
    assert stand_in.name == "lister" and stand_in.attrs["child_session_id"] == child.id
    below = [s for s in trace.spans if s.parent_id == stand_in.id]
    assert {s.kind for s in below} >= {SpanKind.STEP}, "the child's own steps nest under the call"
    nested = [s for s in trace.spans if s.attrs.get("session_id") == child.id]
    assert any(s.kind is SpanKind.TOOL and s.name == "ls" for s in nested)
    assert trace.rollup.subagents == 1
    assert trace.rollup.cost == pytest.approx(0.04), "two model calls of its own, two of its child's"

    runs, _ = await list_runs(env.factory, state.reader, tenant_id="t1", principal_id=None, filters=TraceFilter())  # type: ignore[arg-type]
    assert {r["session_id"] for r in runs} == {sid}, "the child's run is listed inside its parent's only"
    assert sid in {s["session_id"] for s in listed} and child.id not in {s["session_id"] for s in listed}


async def test_a_subagents_approval_is_answered_from_its_parent(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    model(("subagent", {"prompt": "write notes", "description": "writer"}), subagent_calls=[WRITE])
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue), api(env) as http:
        sid = await env.create(state, "githi-approve-deny")  # write waits on a person (2 s)
        await env.sessions.submit(sid, Message("c1", "delegate"))
        async with asyncio.timeout(30):
            while not (pending := (await http.get(f"/v1/sessions/{sid}/approvals", headers=HEADERS)).json()):
                await asyncio.sleep(0.05)
        (asked,) = pending
        inbox = (await http.get("/v1/approvals", headers=HEADERS)).json()
        answered = await http.post(f"/v1/approvals/{asked['approval_id']}", json={"choice": "allow"}, headers=HEADERS)
        events = await env.wait_for(state, sid, run_ended())
    child = await _child_row(env, sid)

    assert asked["subagent"] and asked["subagent_label"] == "writer" and asked["session_id"] == child.id
    (item,) = [i for i in inbox if i["approval_id"] == asked["approval_id"]]
    assert item["root_session_id"] == sid and item["subagent_label"] == "writer"
    assert answered.status_code == 200, answered.text
    assert not _subagent_end(events).is_error
    write = next(e for e in payloads(await state.reader.read(child.id), ToolEnd) if e.tool_name == "write")
    assert not write.is_error, "allowed, so the child's write ran"


# --- background subagents (githi's run_in_background, job_output, job_kill) ------------------


def _notices(events: list) -> list[SubagentNotice]:
    return payloads(events, SubagentNotice)


async def test_a_background_subagent_answers_at_once_and_its_end_wakes_the_session(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    gate = threading.Event()  # the child waits until the parent's first run has ended
    gateway = model(
        ("subagent", {"prompt": "count the files", "description": "counter", "run_in_background": True}),
        subagent_answer="42 files",
        subagent_gate=gate,
        cost_per_call=0.01,
    )
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state)
        await env.sessions.submit(sid, Message("c1", "count them in the background"))
        first = await env.wait_for(state, sid, run_ended())
        child = await _child_row(env, sid)
        gateway.calls = []
        gate.set()
        woken = await env.wait_for(state, sid, run_ended(2))  # the child's end starts a run
        gateway.calls = [("job_output", {"job_id": child.id})]
        await env.sessions.submit(sid, Message("c2", "what did it find?"))
        events = await env.wait_for(state, sid, run_ended(3))
        handle = env.client.get_workflow_handle(workflow_id(child.id))
        await asyncio.wait_for(handle.result(), timeout=30)
        spent = await state.manager._budget.check(sid)

    started = _subagent_end(first).result["payload"]
    assert (started["job_id"], started["status"]) == (child.id, "started")
    assert child.subagent_kind == "background"
    (notice,) = _notices(woken)
    assert notice.child_session_id == child.id
    assert notice.text.startswith(f"Background subagent job {child.id} finished (completed)")
    assert "42 files" in notice.text
    assert any(request[-1].content == notice.text for request in gateway.seen), "the woken run's model read it"
    output = next(e for e in payloads(events, ToolEnd) if e.tool_name == "job_output").result["payload"]
    assert (output["status"], output["text"]) == ("completed", "42 files")
    # Five model calls of the session's own (two, one when woken, two) and the child's one.
    assert spent.total_cost_usd == Decimal("0.06")


async def test_a_subagent_that_settles_while_its_parent_works_is_told_at_the_next_step(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    gateway = model(
        ("subagent", {"prompt": "quick one", "run_in_background": True}), LS, one_at_a_time=True
    )
    state = await env.state()
    with slow_tool("ls", 3.0):  # the child settles while the parent's ls runs
        async with session_worker(env.client, state, task_queue=env.queue):
            sid = await env.create(state)
            await env.sessions.submit(sid, Message("c1", "start it, then list"))
            events = await env.wait_for(state, sid, run_ended())
            await asyncio.sleep(1.5)  # a wrongly woken run would start now
            after = await state.reader.read(sid)

    (notice,) = _notices(events)
    run = events[-1].turn_id
    assert next(e for e in events if isinstance(e.payload, SubagentNotice)).turn_id == run, "told in the run"
    assert any(m.content == notice.text for m in gateway.seen[-1]), "the next step's request had it"
    assert event_types(after).count("RunEnd") == 1, "no run of its own"


async def test_job_kill_stops_a_background_subagent(env: Env, model: Callable[..., ScriptedGateway]) -> None:
    gateway = model(("subagent", {"prompt": "list slowly", "run_in_background": True}), subagent_calls=[LS])
    state = await env.state()
    with slow_tool("ls", 6.0):
        async with session_worker(env.client, state, task_queue=env.queue):
            sid = await env.create(state)
            await env.sessions.submit(sid, Message("c1", "start it"))
            await env.wait_for(state, sid, run_ended())
            child = await _child_row(env, sid)
            await env.wait_for(state, child.id, has_event(lambda p: getattr(p, "tool_name", None) == "ls"))
            gateway.calls = [("job_kill", {"job_id": child.id})]
            await env.sessions.submit(sid, Message("c2", "stop it"))
            events = await env.wait_for(state, sid, has_event(lambda p: isinstance(p, SubagentNotice)), timeout=60)

    killed = next(e for e in payloads(events, ToolEnd) if e.tool_name == "job_kill").result["payload"]
    assert killed["text"] == f"sent cancel to background subagent job {child.id}"
    (notice,) = _notices(events)
    assert "finished (cancelled)" in notice.text
    assert payloads(events, SubagentEnd)[-1].stop_reason == "cancelled"


async def test_a_session_runs_at_most_so_many_background_subagents(
    env: Env, model: Callable[..., ScriptedGateway], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(steps, "MAX_BACKGROUND_JOBS", 1)
    gate = threading.Event()  # the first runs until the test lets it end
    model(
        ("subagent", {"prompt": "first", "run_in_background": True}),
        ("subagent", {"prompt": "second", "run_in_background": True}),
        subagent_gate=gate,
    )
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state)
        await env.sessions.submit(sid, Message("c1", "start two"))
        events = await env.wait_for(state, sid, run_ended())
        gate.set()

    started, refused = [e for e in payloads(events, ToolEnd) if e.tool_name == "subagent"]
    assert started.result["payload"]["status"] == "started"
    assert refused.is_error and refused.result["code"] == "TOO_MANY_JOBS"


async def test_a_stop_reaches_background_subagents_and_their_end_waits_for_the_next_run(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    gateway = model(
        ("subagent", {"prompt": "list slowly", "run_in_background": True}), LS, one_at_a_time=True,
        subagent_calls=[LS],
    )
    state = await env.state()
    with slow_tool("ls", 4.0):
        async with session_worker(env.client, state, task_queue=env.queue):
            sid = await env.create(state)
            await env.sessions.submit(sid, Message("c1", "start it, then list"))
            await env.wait_for(state, sid, has_event(lambda p: getattr(p, "tool_name", None) == "ls"))
            await env.sessions.stop_run(sid, "c1")
            stopped = await env.wait_for(
                state, sid, lambda evs: run_ended()(evs) and has_event(lambda p: isinstance(p, SubagentEnd))(evs), timeout=60
            )
            await asyncio.sleep(2)  # a run started for the notice would be under way now
            quiet = await state.reader.read(sid)
            gateway.calls = []
            await env.sessions.submit(sid, Message("c2", "anything new?"))
            events = await env.wait_for(state, sid, run_ended(2))

    assert payloads(stopped, SubagentEnd)[-1].stop_reason == "cancelled", "the stop reached the child"
    assert event_types(quiet).count("RunStart") == 1 and not _notices(quiet), [
        (type(e.payload).__name__, (e.turn_id or "")[:6], str(e.payload)[:110]) for e in quiet[-14:]
    ]
    (notice,) = _notices(events)
    assert notice.text.startswith("Background subagent job") and "(cancelled)" in notice.text


async def test_a_background_subagent_works_in_a_named_hand_of_its_session(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    gateway = model(
        ("subagent", {"prompt": "build it", "run_in_background": True}),
        subagent_calls=[("write", {"path": "built.txt", "content": "made by the child", "hand": "build"})],
    )
    state = await env.state(workspace_store=MemoryWorkspaceStore(objects=MemoryObjectStorage()))
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await create_with_hands(env, state, "build")
        await env.sessions.submit(sid, Message("c1", "have a child build"))
        await env.wait_for(state, sid, run_ended(2))  # the child's end woke the session
        built = state.manager._hands.sandbox(hand_key(sid, "build"))
        assert built is not None and await built.read_file("built.txt") == b"made by the child"
        default = state.manager._hands.sandbox(sid)
        assert default is None or not await default.exists("built.txt"), "it stayed in its hand"
    assert any("hand" in (s["function"]["parameters"].get("properties") or {}) for s in gateway.tools_seen[0])


async def test_erasing_a_session_stops_and_erases_its_background_subagents(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    gate = threading.Event()  # the child is busy until the test lets it go
    model(("subagent", {"prompt": "work", "run_in_background": True}), subagent_gate=gate, subagent_calls=[WRITE])
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue), api(env) as http:
        sid = await env.create(state)
        await env.sessions.submit(sid, Message("c1", "start it"))
        await env.wait_for(state, sid, run_ended())
        child = await _child_row(env, sid)
        erased = await http.delete(f"/v1/sessions/{sid}/data", headers=HEADERS)
        gate.set()
        assert erased.status_code == 204, erased.text
        await asyncio.sleep(1.5)  # anything still in flight would land now
        assert await state.reader.read(child.id) == []
    async with env.factory() as db:  # type: ignore[operator]
        assert await db.get(SessionRow, child.id) is None


# --- continuable subagents (githi's subagent_continuable, send_message, interrupt_agent,
# list_agents, report) ----------------------------------------------------------------------


async def _quiet(env: Env, state: SupervisorState, sid: str, runs: int) -> list:
    """The session's journal once ``runs`` runs ended and nothing more started for a moment."""
    events = await env.wait_for(state, sid, run_ended(runs), timeout=60)
    while True:
        await asyncio.sleep(1.0)
        later = await state.reader.read(sid)
        if event_types(later).count("RunStart") == event_types(later).count("RunEnd") and len(later) == len(events):
            return later
        events = later


async def test_a_continuable_subagent_reports_takes_more_turns_and_stays(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    gate = threading.Event()  # the child starts once its parent's first run has ended
    gateway = model(
        ("subagent_continuable", {"prompt": "first task", "description": "helper"}),
        subagent_calls=[("report", {"output": "halfway: found 3"})],
        subagent_answer="turn done",
        subagent_gate=gate,
    )
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state)
        await env.sessions.submit(sid, Message("c1", "start a helper"))
        first = await env.wait_for(state, sid, run_ended())
        child = await _child_row(env, sid)
        gateway.calls = []
        gate.set()
        told = await _quiet(env, state, sid, 2)  # its report and its settling woke the session
        gateway.calls = [("send_message", {"subagent_id": child.id, "message": "now the second task"}),
                         ("list_agents", {})]
        await env.sessions.submit(sid, Message("c2", "give it more"))
        runs = event_types(told).count("RunEnd")
        events = await _quiet(env, state, sid, runs + 2)  # this run, and its settling again
        child_events = await state.reader.read(child.id)
        status = (await env.client.get_workflow_handle(workflow_id(child.id)).describe()).status
        await env.sessions.terminate(sid)
        handle = env.client.get_workflow_handle(workflow_id(child.id))
        await asyncio.wait_for(handle.result(), timeout=30)  # it ends with its parent

    started = next(e for e in payloads(first, ToolEnd) if e.tool_name == "subagent_continuable").result["payload"]
    assert (started["subagent_id"], started["kind"]) == (child.id, "continuable")
    assert child.subagent_kind == "continuable"
    notices = [n.text for n in _notices(events)]
    assert f"Background subagent {child.id} reported:\nhalfway: found 3" in notices
    settled = [n for n in notices if "will do no further work unless you send it more" in n]
    assert len(settled) == 2 and all("turn done" in n for n in settled), notices
    asks = [m.content[0]["text"] for m in payloads(child_events, MessageEnd) if m.role == "user"]
    assert asks == ["first task", "now the second task"], "the same child, a second turn"
    assert status is WorkflowExecutionStatus.RUNNING, "it stays while its parent is open"
    sent = next(e for e in payloads(events, ToolEnd) if e.tool_name == "send_message").result["payload"]
    assert sent["subagent_id"] == child.id
    listed = next(e for e in payloads(events, ToolEnd) if e.tool_name == "list_agents").result["payload"]
    assert [(a["id"], a["label"]) for a in listed["agents"]] == [(child.id, "helper")]


async def test_interrupt_agent_stops_the_turn_and_keeps_what_is_queued(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    gateway = model(("subagent_continuable", {"prompt": "list slowly"}), subagent_calls=[LS])
    state = await env.state()
    with slow_tool("ls", 4.0):
        async with session_worker(env.client, state, task_queue=env.queue):
            sid = await env.create(state)
            await env.sessions.submit(sid, Message("c1", "start it"))
            await env.wait_for(state, sid, run_ended())
            child = await _child_row(env, sid)
            await env.wait_for(state, child.id, has_event(lambda p: getattr(p, "tool_name", None) == "ls"))
            gateway.calls = [("send_message", {"subagent_id": child.id, "message": "then this"}),
                             ("interrupt_agent", {"agent_id": child.id})]
            await env.sessions.submit(sid, Message("c2", "queue one more, stop this one"))
            child_events = await env.wait_for(state, child.id, run_ended(2), timeout=60)

    assert [t.stop_reason for t in payloads(child_events, TurnEnd)] == ["aborted", "stop"]
    asks = [m.content[0]["text"] for m in payloads(child_events, MessageEnd) if m.role == "user"]
    assert asks == ["list slowly", "then this"], "the queued message ran after the stop"



async def _told(env: Env, state: SupervisorState, sid: str, notices: int) -> list:
    """The session's journal once it was told ``notices`` times and every run it started
    for them (or that took them in at a step) has ended."""
    events = await env.wait_for(state, sid, lambda evs: len(_notices(evs)) >= notices, timeout=60)
    return await _quiet(env, state, sid, event_types(events).count("RunStart"))

async def test_a_person_reads_the_subagents_and_messages_only_a_continuable_one(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    model(
        ("subagent", {"prompt": "write the notes file", "description": "notes"}),
        ("subagent_continuable", {"prompt": "stand by", "description": "helper"}),
        cost_per_call=0.01,
        subagent_answer="done it",
    )
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue), api(env) as http:
        sid = await env.create(state)
        other = await env.create(state)
        await env.sessions.submit(sid, Message("c1", "start two"))
        await _told(env, state, sid, 1)  # the helper's first turn settled
        # The round started both at once: which was created first is not fixed.
        started = (await http.get(f"/v1/sessions/{sid}/subagents", headers=HEADERS)).json()["subagents"]
        one_shot, helper = sorted(started, key=lambda a: a["kind"] == "continuable")
        await asyncio.wait_for(env.client.get_workflow_handle(workflow_id(one_shot["id"])).result(), timeout=30)
        listed = {a["id"]: a for a in (await http.get(f"/v1/sessions/{sid}/subagents", headers=HEADERS)).json()["subagents"]}
        single = await http.get(f"/v1/sessions/{sid}/subagents/{helper['id']}", headers=HEADERS)
        not_below = await http.get(f"/v1/sessions/{other}/subagents/{helper['id']}", headers=HEADERS)
        refused = await http.post(
            f"/v1/sessions/{one_shot['id']}/messages", json={"command_id": "p1", "text": "more"}, headers=HEADERS
        )
        sent = await http.post(
            f"/v1/sessions/{helper['id']}/messages", json={"command_id": "p2", "text": "one more"}, headers=HEADERS
        )
        helper_events = await env.wait_for(state, helper["id"], run_ended(2))
        await _told(env, state, sid, 2)  # and its second
        after = (await http.get(f"/v1/sessions/{sid}/subagents/{helper['id']}", headers=HEADERS)).json()
        await env.sessions.terminate(sid)

    done, kept = listed[one_shot["id"]], listed[helper["id"]]
    assert (done["id"], done["parent_id"], done["depth"]) == (one_shot["id"], sid, 1)
    assert (done["label"], done["task"], done["kind"], done["mode"]) == ("notes", "write the notes file", "foreground", "spawn")
    assert (done["status"], done["outcome"], done["answer"], done["open"]) == ("completed", "completed", "done it", False)
    assert done["call_id"] and done["cost_usd"] == pytest.approx(0.01)
    assert (kept["label"], kept["task"], kept["kind"]) == ("helper", "stand by", "continuable")
    assert (kept["status"], kept["outcome"], kept["answer"], kept["open"]) == ("ready", "completed", "done it", True)
    assert single.status_code == 200 and single.json() == kept
    assert not_below.status_code == 404, "a subagent is read only from the session above it"
    assert refused.status_code == 409 and refused.json()["detail"]["reason"] == "not_messageable"
    assert sent.status_code == 202, sent.text
    asks = [m.content[0]["text"] for m in payloads(helper_events, MessageEnd) if m.role == "user"]
    assert asks == ["stand by", "one more"], "the person's message is the helper's next turn"
    assert (after["status"], after["cost_usd"]) == ("ready", pytest.approx(0.02))
