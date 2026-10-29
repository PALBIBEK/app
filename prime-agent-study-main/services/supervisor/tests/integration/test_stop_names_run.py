"""A stop names its run, as pi-temporal's ``interrupt`` names its prompt: a stop that lands as
its run ends cannot stop the next one, and stopping needs no worker to say what runs.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

from supervisor.agent.events import ToolEnd, ToolStart, TurnEnd
from supervisor.temporal.contracts import Message
from supervisor.temporal.worker import session_worker
from tests.support.api import supervisor_api
from tests.support.githi_env import SLOW_HEARTBEAT, WRITE, Env, payloads, run_ended, spawn_worker
from tests.support.scripted import ScriptedGateway, slow_tool

pytestmark = [pytest.mark.asyncio(loop_scope="session"), pytest.mark.usefixtures("memory_service_url")]


def _calls_started(n: int) -> Callable[[list], bool]:
    return lambda events: len(payloads(events, ToolStart)) >= n


def _stop_reasons(events: list) -> list[str]:
    return [e.stop_reason for e in payloads(events, TurnEnd)]


async def test_a_stop_for_a_run_that_ended_does_not_stop_the_next(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    model(WRITE)
    state = await env.state()
    with slow_tool("write", 2.0):
        async with session_worker(env.client, state, task_queue=env.queue):
            sid = await env.create(state)
            await env.sessions.submit(sid, Message("c1", "first"))
            await env.sessions.submit(sid, Message("c2", "second"))  # queued behind c1
            await env.wait_for(state, sid, _calls_started(2))  # c1 has ended, c2 runs
            await env.sessions.stop_run(sid, "c1")
            await env.sessions.stop_run(sid, "never-sent")
            events = await env.wait_for(state, sid, run_ended(2), timeout=30)
            assert _stop_reasons(events) == ["stop", "stop"]

            await env.sessions.submit(sid, Message("c3", "third"))
            await env.wait_for(state, sid, _calls_started(3))
            await env.sessions.stop_run(sid, "c3")
            events = await env.wait_for(state, sid, run_ended(3), timeout=30)
    assert _stop_reasons(events) == ["stop", "stop", "aborted"]


async def test_a_run_is_cancelled_while_no_worker_is_up(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    """The cancel is accepted at once with every worker down; the run ends aborted once a
    worker comes. Before, the cancel asked the session which run was going, and failed."""
    model(WRITE)
    state = await env.state()
    state.workflows = env.sessions  # the API drives sessions through their workflows
    await env.composition(state, "githi-v1")  # before the worker boots: it hydrates it
    worker, _ = await spawn_worker(env, {"scripted_calls": [list(WRITE)], "slow_tool": ["write", 120]})
    async with supervisor_api(state) as api:
        try:
            sid = await env.create(state, timing=SLOW_HEARTBEAT)
            sent = await api.post(f"/v1/sessions/{sid}/messages", json={"command_id": "c1", "text": "write"})
            await env.wait_for(state, sid, _calls_started(1))
        finally:
            worker.kill()
            await worker.wait()
        async with asyncio.timeout(20):
            cancelled = await api.post(f"/v1/sessions/{sid}/runs/{sent.json()['turn_id']}/cancel")
        assert cancelled.status_code == 202, cancelled.text  # ends once a worker takes the stop

    async with session_worker(env.client, await env.state(), task_queue=env.queue):
        events = await env.wait_for(state, sid, run_ended(), timeout=60)
    assert _stop_reasons(events) == ["aborted"]
    (end,) = payloads(events, ToolEnd)
    assert end.result["code"] == "TOOL_OUTCOME_UNKNOWN"
