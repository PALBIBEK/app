"""A busy worker is not a lost one. A round runs a bounded number of calls at once, so one
session cannot take its worker's slots; and a session whose worker misses the placement limit
but still heartbeats waits for it instead of being adopted (which would close its running
at-most-once calls as unknown).
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator, Callable

import pytest

from supervisor.agent.events import ToolEnd, ToolStart
from supervisor.app import SupervisorState
from supervisor.temporal.contracts import Activity, Message
from supervisor.temporal.fleet import WorkerBeat, heartbeat_loop
from supervisor.temporal.worker import session_worker
from supervisor.temporal.workflow import ROUND_CONCURRENCY
from tests.support.githi_env import Env, payloads, run_ended
from tests.support.scripted import ScriptedGateway, slow_tool

pytestmark = [pytest.mark.asyncio(loop_scope="session"), pytest.mark.usefixtures("memory_service_url")]


def _write(path: str) -> tuple[str, dict]:
    return ("write", {"file_path": path, "content": path})


def _most_at_once(events: list) -> int:
    running = most = 0
    for event in events:
        if isinstance(event.payload, ToolStart):
            running += 1
            most = max(most, running)
        elif isinstance(event.payload, ToolEnd):
            running -= 1
    return most


@contextlib.asynccontextmanager
async def _beating(state: SupervisorState, queue: str, home: str) -> AsyncIterator[None]:
    """This process heartbeats as the worker behind ``home``, as a worker process does."""
    assert state.heartbeats is not None
    started = int(time.time() * 1000)

    def beat() -> WorkerBeat:
        return WorkerBeat(
            worker_id=f"busy-{home}", host="test", task_queue=queue, home_queue=home, max_runs=1,
            busy=1, started_at_ms=started, last_seen_ms=int(time.time() * 1000),
        )

    stop = asyncio.Event()
    beats = asyncio.create_task(heartbeat_loop(state.heartbeats, beat, stop, interval_s=0.5))
    try:
        yield
    finally:
        stop.set()
        await beats


async def test_a_round_runs_a_bounded_number_of_calls_at_once(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    model(*(_write(f"f{i}.txt") for i in range(ROUND_CONCURRENCY + 4)))
    state = await env.state()
    with slow_tool("write", 1.0):
        async with session_worker(env.client, state, task_queue=env.queue):
            sid = await env.create(state)
            await env.sessions.submit(sid, Message("c1", "write them all"))
            events = await env.wait_for(state, sid, run_ended(), timeout=60)
    ends = payloads(events, ToolEnd)
    assert len(ends) == ROUND_CONCURRENCY + 4 and not any(e.is_error for e in ends)
    assert _most_at_once(events) == ROUND_CONCURRENCY


async def test_a_session_waits_for_its_busy_worker_instead_of_moving(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    """One activity slot, two at-most-once writes: the second waits past the placement limit
    behind the first. Its worker still beats, so the session stays and both writes land."""
    model(_write("a.txt"), _write("b.txt"))
    state = await env.state()
    with slow_tool("write", 5.0):
        async with session_worker(
            env.client, state, task_queue=env.queue, max_concurrent_runs=1
        ) as worker, _beating(state, env.queue, worker.home_queue):
            sid = await env.create(state, "githi-once")
            await env.sessions.submit(sid, Message("c1", "write both"))
            events = await env.wait_for(state, sid, run_ended(), timeout=60)
            scheduled = await env.activity_types(sid)
    assert Activity.ADOPT not in scheduled, "a busy worker's session was moved"
    assert Activity.WORKER_BEATS in scheduled, "the second write did wait past the limit"
    assert [e.is_error for e in payloads(events, ToolEnd)] == [False, False]
