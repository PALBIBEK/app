"""A run's bounds as pi-temporal keeps them: tokens per run and per session, counted by
the workflow from each model step (any worker), and a hard deadline that stops the run
where it is, mid-call. A run past a bound ends saying so; the session goes on.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

import pytest

from supervisor.agent.events import ErrorEvent, MessageEnd, ToolEnd, ToolStart, TurnEnd
from supervisor.temporal.contracts import Message, Phase
from supervisor.temporal.steps import UNKNOWN_OUTCOME
from supervisor.temporal.worker import session_worker
from tests.support.githi_env import LS, WRITE, Env, has_event, payloads, run_ended
from tests.support.scripted import ScriptedGateway, slow_tool

pytestmark = [pytest.mark.asyncio(loop_scope="session"), pytest.mark.usefixtures("memory_service_url")]


def _answers(events: list) -> int:
    return sum(1 for e in payloads(events, MessageEnd) if e.role == "assistant")


async def _idle(env: Env, sid: str) -> None:
    async with asyncio.timeout(10):
        while (await env.sessions.status(sid)).phase is not Phase.IDLE:
            await asyncio.sleep(0.1)


async def test_a_run_past_its_token_bound_ends_and_the_session_goes_on(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    model(LS, LS, LS, LS, one_at_a_time=True)  # 15 tokens a step; the bound is 20
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state, "githi-run-tokens")
        await env.sessions.submit(sid, Message("c1", "list four times"))
        events = await env.wait_for(state, sid, run_ended())
        assert [e.error_class for e in payloads(events, ErrorEvent)] == ["RunTokenLimit"]
        assert _answers(events) == 2, "it stopped at the first step past the bound"
        await _idle(env, sid)
        await env.sessions.submit(sid, Message("c2", "again"))  # not refused: the run ended, not the session
        events = await env.wait_for(state, sid, run_ended(2))
    assert payloads(events, TurnEnd)[-1].stop_reason == "error"


async def test_a_session_past_its_token_bound_runs_no_step_for_a_new_message(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    gateway = model(LS)  # a run: a call and an answer, 30 tokens; the session's bound is 40
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state, "githi-session-tokens")
        await env.sessions.submit(sid, Message("c1", "list"))
        events = await env.wait_for(state, sid, run_ended())
        assert not payloads(events, ErrorEvent)
        await _idle(env, sid)
        await env.sessions.submit(sid, Message("c2", "list again"))  # 45 after its first step
        events = await env.wait_for(state, sid, run_ended(2))
        assert [e.error_class for e in payloads(events, ErrorEvent)] == ["SessionTokenLimit"]
        await _idle(env, sid)
        asked = len(gateway.seen)
        await env.sessions.submit(sid, Message("c3", "once more"))
        events = await env.wait_for(state, sid, run_ended(3))
    assert len(gateway.seen) == asked, "a session past its bound asked the model nothing"
    assert [e.error_class for e in payloads(events, ErrorEvent)] == ["SessionTokenLimit"] * 2


async def test_a_hard_deadline_stops_the_run_mid_call(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    model(WRITE)
    state = await env.state()
    with slow_tool("write", 60):
        async with session_worker(env.client, state, task_queue=env.queue):
            sid = await env.create(state, "githi-deadline")
            started = time.monotonic()
            await env.sessions.submit(sid, Message("c1", "write slowly"))
            await env.wait_for(state, sid, has_event(lambda p: isinstance(p, ToolStart)))
            events = await env.wait_for(state, sid, run_ended(), timeout=20)
            took = time.monotonic() - started
            await _idle(env, sid)

    assert took < 15, f"the deadline (2 s) did not stop a 60 s call: {took:.1f}s"
    assert [e.error_class for e in payloads(events, ErrorEvent)] == ["RunDeadline"]
    (end,) = payloads(events, ToolEnd)
    assert end.result["error"] == UNKNOWN_OUTCOME
