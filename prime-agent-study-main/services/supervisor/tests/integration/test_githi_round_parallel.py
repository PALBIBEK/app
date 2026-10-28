"""A step's tool calls run at once, as pi's (and pi-temporal's stepped mode) do.

Each call is its own activity, started together; calls naming the same file run in
the order the model made them (pi's file mutation queue). A stop answers every call
of the round: an unknown outcome for one that started, "did not run" for the rest.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from supervisor.agent.events import ErrorEvent, ToolEnd, ToolStart, TurnEnd
from supervisor.temporal.contracts import Activity, Message
from supervisor.temporal.steps import BEHIND_UNKNOWN, NOT_RUN_OUTCOME, UNKNOWN_OUTCOME
from supervisor.temporal.worker import session_worker
from tests.support.githi_env import LS, WRITE, Env, has_event, payloads, run_ended, spawn_worker
from tests.support.scripted import ScriptedGateway, slow_tool

pytestmark = [pytest.mark.asyncio(loop_scope="session"), pytest.mark.usefixtures("memory_service_url")]

GREP = ("grep", {"pattern": "hello", "path": "."})
READ = ("read", {"path": "notes.txt"})
WRITE_B = ("write", {"path": "b.txt", "content": "bee"})


def _seq(events: list, kind: type, call_id: str) -> int:
    return next(e.seq for e in events if isinstance(e.payload, kind) and e.payload.tool_call_id == call_id)


async def test_a_rounds_calls_run_at_once_each_as_its_own_activity(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    gateway = model(WRITE, WRITE_B, LS)
    state = await env.state()
    with slow_tool("write", 1.5):
        async with session_worker(env.client, state, task_queue=env.queue):
            sid = await env.create(state)
            await env.sessions.submit(sid, Message("c1", "write both, list"))
            events = await env.wait_for(state, sid, run_ended())
            scheduled = await env.activity_types(sid)

    assert scheduled.count(Activity.TOOL_CALL) == 3 and Activity.TOOL_BATCH not in scheduled
    first_end = min(e.seq for e in events if isinstance(e.payload, ToolEnd) and e.payload.tool_name == "write")
    assert _seq(events, ToolStart, "call-0-0") < first_end and _seq(events, ToolStart, "call-0-1") < first_end, (
        "the second write waited for the first"
    )
    assert not any(e.is_error for e in payloads(events, ToolEnd))
    shown = [m.tool_call_id for m in gateway.seen[-1] if m.role == "tool"]
    assert shown == ["call-0-0", "call-0-1", "call-0-2"]  # read back as asked
    assert payloads(events, TurnEnd)[-1].stop_reason == "stop"


async def test_calls_on_one_file_run_in_the_order_made(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    model(WRITE, READ, GREP)
    state = await env.state()
    with slow_tool("write", 1.0):
        async with session_worker(env.client, state, task_queue=env.queue):
            sid = await env.create(state)
            await env.sessions.submit(sid, Message("c1", "write it, then read it"))
            events = await env.wait_for(state, sid, run_ended())

    ends = {e.tool_name: e for e in payloads(events, ToolEnd)}
    assert ends["read"].result["payload"] == "hello", "the read saw the write"
    assert _seq(events, ToolStart, "call-0-1") > _seq(events, ToolEnd, "call-0-0")
    assert _seq(events, ToolStart, "call-0-2") < _seq(events, ToolEnd, "call-0-0"), (
        "a call on another file did not wait"
    )


async def test_a_change_waits_for_the_reads_of_its_file_made_before_it(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    model(WRITE, READ, ("write", {"path": "notes.txt", "content": "second"}), READ)
    state = await env.state()
    with slow_tool("read", 0.5):
        async with session_worker(env.client, state, task_queue=env.queue):
            sid = await env.create(state)
            await env.sessions.submit(sid, Message("c1", "write, read, rewrite, read"))
            events = await env.wait_for(state, sid, run_ended())

    ends = {e.tool_call_id: e for e in payloads(events, ToolEnd)}
    assert ends["call-0-1"].result["payload"] == "hello", "the first read saw the first write only"
    assert ends["call-0-3"].result["payload"] == "second"
    assert _seq(events, ToolStart, "call-0-2") > _seq(events, ToolEnd, "call-0-1")


async def test_a_stop_answers_every_call_of_the_round(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    model(WRITE, READ)
    state = await env.state()
    with slow_tool("write", 60):
        async with session_worker(env.client, state, task_queue=env.queue):
            sid = await env.create(state)
            await env.sessions.submit(sid, Message("c1", "write slowly, then read"))
            await env.wait_for(state, sid, has_event(lambda p: isinstance(p, ToolStart)))
            await env.sessions.abort(sid)
            events = await env.wait_for(state, sid, run_ended(), timeout=20)

    assert payloads(events, TurnEnd)[-1].stop_reason == "aborted"
    ends = {e.tool_call_id: e for e in payloads(events, ToolEnd)}
    assert ends["call-0-0"].result["error"] == UNKNOWN_OUTCOME  # it started: it may have run
    assert ends["call-0-1"].result["error"] == NOT_RUN_OUTCOME  # it was waiting on the write
    assert all(e.is_error for e in ends.values())


async def test_a_worker_lost_mid_round_answers_its_calls_and_the_run_goes_on(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    """Two at-most-once writes lost together are each answered as unknown, a change queued
    behind one of them is not run (it was made assuming the first took effect), the
    session is adopted, not failed, and the model is told and answers."""
    rewrite = ("write", {"file_path": "notes.txt", "content": "second"})
    gateway = model(WRITE, WRITE_B, rewrite)
    observer = await env.state()
    await env.composition(observer, "githi-once")  # before the worker boots: it hydrates it
    worker, _ = await spawn_worker(
        env, {"scripted_calls": [list(WRITE), list(WRITE_B), list(rewrite)], "slow_tool": ["write", 120]}
    )
    try:
        sid = await env.create(observer, "githi-once")
        await env.sessions.submit(sid, Message("c1", "write both, then rewrite the first"))
        await env.wait_for(
            observer, sid, lambda events: len(payloads(events, ToolStart)) >= 2, timeout=30
        )
    finally:
        worker.kill()  # SIGKILL while both writes run
        await worker.wait()

    adopter = await env.state()
    async with session_worker(env.client, adopter, task_queue=env.queue):
        events = await env.wait_for(observer, sid, run_ended(), timeout=90)
        scheduled = await env.activity_types(sid)

    ends = {e.tool_call_id: e for e in payloads(events, ToolEnd)}
    assert ends["call-0-0"].result["error"] == UNKNOWN_OUTCOME
    assert ends["call-0-1"].result["error"] == UNKNOWN_OUTCOME
    assert ends["call-0-2"].result["error"] == BEHIND_UNKNOWN, "a change behind a lost one is not run"
    assert not payloads(events, ErrorEvent), "the session was adopted, not failed"
    assert scheduled.count(Activity.ADOPT) == 1
    assert payloads(events, TurnEnd)[-1].stop_reason == "stop"
    read = [m.content for m in gateway.seen[-1] if m.role == "tool"]
    assert read == [UNKNOWN_OUTCOME, UNKNOWN_OUTCOME, BEHIND_UNKNOWN], "told in call order"
