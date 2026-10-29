"""A run's bounds as pi-temporal keeps them: tokens per run and per session, counted by
the workflow from each model step (any worker), and a hard deadline that stops the run
where it is, mid-call. A run past a bound ends saying so; the session goes on.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from decimal import Decimal
from typing import Any
from unittest.mock import patch

import pytest

from supervisor.agent.events import ErrorEvent, MessageEnd, ToolEnd, ToolStart, TurnEnd
from supervisor.session.manager import SessionManager
from supervisor.temporal.contracts import Message, Phase
from supervisor.temporal.steps import NOT_RUN_OUTCOME, UNKNOWN_OUTCOME, GithiSteps
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


async def test_a_deadline_while_nothing_can_be_cancelled_ends_the_run_before_its_next_step(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    """The deadline fires while the user's words are placed in the run (a step it does not
    cancel): the run ends there, and the model is not asked again."""
    gateway = model(WRITE)
    state = await env.state()
    place = GithiSteps.place_inputs

    async def slow_place(self: GithiSteps, req: Any) -> None:
        await asyncio.sleep(3)  # the deadline (2 s) fires meanwhile
        await place(self, req)

    with slow_tool("write", 1.0), patch.object(GithiSteps, "place_inputs", slow_place):
        async with session_worker(env.client, state, task_queue=env.queue):
            sid = await env.create(state, "githi-deadline")
            await env.sessions.submit(sid, Message("c1", "write it"))
            await env.wait_for(state, sid, has_event(lambda p: isinstance(p, ToolStart)))
            accepted = await env.sessions.submit(sid, Message("c2", "and look at this", steer=True))
            assert accepted.steered
            events = await env.wait_for(state, sid, run_ended(), timeout=30)
            await _idle(env, sid)

    assert [e.error_class for e in payloads(events, ErrorEvent)] == ["RunDeadline"]
    assert len(gateway.seen) == 1, "the model was asked again after the deadline"


async def test_a_stop_after_the_model_asked_for_calls_answers_each_as_not_run(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    """The abort lands while the checkpoint beside a finished model step still runs (a
    step it does not cancel): the run ends before its round, and the round's call is
    answered as not run rather than left without a result."""
    gateway = model(WRITE)
    state = await env.state()
    checkpoint = SessionManager.checkpoint

    async def slow_checkpoint(self: SessionManager, session_id: str) -> Any:
        await asyncio.sleep(3)
        return await checkpoint(self, session_id)

    with patch.object(SessionManager, "checkpoint", slow_checkpoint):
        async with session_worker(env.client, state, task_queue=env.queue):
            sid = await env.create(state)
            await env.sessions.submit(sid, Message("c1", "write it"))
            await env.wait_for(state, sid, has_event(lambda p: isinstance(p, MessageEnd) and p.role == "assistant"))
            await env.sessions.abort(sid)
            events = await env.wait_for(state, sid, run_ended(), timeout=30)
            await _idle(env, sid)

    assert payloads(events, TurnEnd)[-1].stop_reason == "aborted"
    (end,) = payloads(events, ToolEnd)
    assert end.result["error"] == NOT_RUN_OUTCOME
    assert [s.not_run for s in payloads(events, ToolStart)] == [True], "nothing ran it"
    assert len(gateway.seen) == 1


async def test_a_session_past_its_time_bound_runs_no_step_and_idle_time_is_free(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    """pi-temporal's session time bound: the time the session's runs take, idle time not
    counted. The bound is 2 s and each run's write takes 1.2 s."""
    gateway = model(WRITE)
    state = await env.state()
    with slow_tool("write", 1.2):
        async with session_worker(env.client, state, task_queue=env.queue):
            sid = await env.create(state, "githi-session-time")
            await env.sessions.submit(sid, Message("c1", "write"))
            await env.wait_for(state, sid, run_ended(1))
            await _idle(env, sid)
            await asyncio.sleep(3)  # idle: free
            await env.sessions.submit(sid, Message("c2", "write again"))  # past it after its write
            events = await env.wait_for(state, sid, run_ended(2))
            assert len(payloads(events, ToolEnd)) == 2, "idle time was counted"
            assert [e.error_class for e in payloads(events, ErrorEvent)] == ["SessionTimeLimit"]
            await _idle(env, sid)
            asked = len(gateway.seen)
            await env.sessions.submit(sid, Message("c3", "once more"))
            events = await env.wait_for(state, sid, run_ended(3))
            used = (await env.sessions.status(sid)).seconds_used
    assert len(gateway.seen) == asked, "a session past its bound asked the model"
    assert [e.error_class for e in payloads(events, ErrorEvent)] == ["SessionTimeLimit"] * 2
    assert 2.4 <= used < 5, used


async def test_a_model_calls_cost_is_counted_once(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    """The session's cost (what its cost cap and its tenant's totals read) holds each model
    call's cost once: it was added by the budget tracker and again as the reply was
    journalled, so a cap tripped at half the spend it allowed."""
    gateway = model(LS)  # a run: a call, then an answer
    gateway.cost_per_call = 0.01
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state)
        await env.sessions.submit(sid, Message("c1", "list"))
        await env.wait_for(state, sid, run_ended())
    assert (await state.manager._budget.check(sid)).total_cost_usd == Decimal("0.02")
