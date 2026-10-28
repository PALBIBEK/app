"""A step's tool calls as one round: approvals first, read-only calls together.

Against a real Temporal server, with a scripted gateway asking for several calls in
one reply.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from supervisor.agent.events import ApprovalRequired, ToolEnd, ToolStart, TurnEnd
from supervisor.temporal.contracts import Activity, Message
from supervisor.temporal.worker import session_worker
from tests.support.githi_env import LS, WRITE, Env, has_event, payloads, run_ended
from tests.support.scripted import ScriptedGateway

pytestmark = [pytest.mark.asyncio(loop_scope="session"), pytest.mark.usefixtures("memory_service_url")]

GREP = ("grep", {"pattern": "hello", "path": "."})
READ = ("read", {"path": "notes.txt"})


async def test_read_only_calls_run_together_and_are_read_back_in_order(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    gateway = model(LS, GREP, ("ls", {"path": "src"}))
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state)
        await env.sessions.submit(sid, Message("c1", "look around"))
        events = await env.wait_for(state, sid, run_ended())
        scheduled = await env.activity_types(sid)

    assert scheduled.count(Activity.TOOL_BATCH) == 1
    assert Activity.TOOL_CALL not in scheduled
    assert len(payloads(events, ToolEnd)) == 3
    shown = [m.tool_call_id for m in gateway.seen[-1] if m.role == "tool"]
    assert shown == ["call-0-0", "call-0-1", "call-0-2"]  # as asked, whatever finished first
    assert payloads(events, TurnEnd)[-1].stop_reason == "stop"


async def test_a_call_that_changes_files_runs_alone_and_before_what_follows(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    model(WRITE, READ, GREP)
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state)
        await env.sessions.submit(sid, Message("c1", "write it, then read it"))
        events = await env.wait_for(state, sid, run_ended())
        scheduled = await env.activity_types(sid)

    assert scheduled.count(Activity.TOOL_CALL) == 1 and scheduled.count(Activity.TOOL_BATCH) == 1
    ends = {e.tool_name: e for e in payloads(events, ToolEnd)}
    assert ends["read"].result["payload"] == "hello"  # the write landed first
    starts = [e.seq for e in events if isinstance(e.payload, ToolStart)]
    write_end = next(e.seq for e in events if isinstance(e.payload, ToolEnd) and e.payload.tool_name == "write")
    assert all(seq > write_end for seq in starts[1:])


async def test_every_approval_is_asked_before_any_call_runs(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    model(LS, WRITE)
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state, "githi-approve-allow")
        await env.sessions.submit(sid, Message("c1", "list, then write"))
        events = await env.wait_for(state, sid, has_event(lambda p: isinstance(p, ApprovalRequired)))
        assert not payloads(events, ToolStart), "a call ran before the round's approvals"
        approval_id = payloads(events, ApprovalRequired)[0].approval_id
        await env.sessions.respond_approval(sid, approval_id, "allow")
        events = await env.wait_for(state, sid, run_ended())

    assert [e.tool_name for e in payloads(events, ToolEnd)] == ["ls", "write"]
    assert not any(e.is_error for e in payloads(events, ToolEnd))
