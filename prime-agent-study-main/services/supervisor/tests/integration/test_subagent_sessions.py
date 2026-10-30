"""Subagents are sessions, as githi's are agents of their own (``supervisor.session.children``).

A ``subagent`` call starts a child session that runs its own loop with its parent's tools,
working in its parent's files; the parent reads the child's answer. A fork carries the
parent's completed turns. A failed child is graded as githi grades it, a stop reaches the
child too, and the child's spend counts toward its parent's.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from temporalio.client import WorkflowExecutionStatus
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Replayer

from supervisor.agent.events import MessageEnd, SubagentEnd, SubagentStart, ToolEnd, TurnEnd
from supervisor.db.models import SessionIndexRow, SessionRow
from supervisor.main import create_app
from supervisor.session.children import SUBAGENT_BRIEF
from supervisor.temporal.contracts import Message, workflow_id
from supervisor.temporal.job_workflow import SubagentJobWorkflow
from supervisor.temporal.worker import session_worker
from supervisor.temporal.workflow import AgentSessionWorkflow
from supervisor.traces.kinds import SpanKind
from supervisor.traces.store import TraceFilter, list_runs, load_run
from tests.support.githi_env import LS, WRITE, Env, has_event, payloads, run_ended
from tests.support.scripted import ScriptedGateway, slow_tool
from tests.support.workflow_histories import replayable_histories

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


async def test_subagent_histories_replay_deterministically(env: Env) -> None:
    replayer = Replayer(workflows=[AgentSessionWorkflow, SubagentJobWorkflow], data_converter=pydantic_data_converter)
    replayed = 0
    async for history in replayable_histories(env.client, "WorkflowType = 'AgentSession'"):
        result = await replayer.replay_workflow(history, raise_on_replay_failure=False)
        assert result.replay_failure is None, result.replay_failure
        replayed += 1
    assert replayed >= 1


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
