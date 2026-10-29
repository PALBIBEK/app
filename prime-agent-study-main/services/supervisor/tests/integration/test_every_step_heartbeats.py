"""Every step heartbeats, so a worker that dies in the middle of a short step is found in
seconds, not after the step's whole time limit (pi-temporal: 30 s on every step).

Found by the chaos test: a worker killed after closing a run journalled its end but before it
reported, and the next message waited out the close's 2-minute limit.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

import pytest

from supervisor.temporal.contracts import Message
from supervisor.temporal.worker import session_worker
from tests.support.githi_env import Env, has_event, run_ended, spawn_worker
from tests.support.scripted import ScriptedGateway

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_a_worker_killed_while_closing_a_run_is_found_in_seconds(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    model()  # the model answers at once; closing the run is what takes the time
    observer = await env.state()
    await env.composition(observer, "githi-v1")
    worker, _ = await spawn_worker(env, {"scripted_calls": [], "slow_end_run": 120})
    try:
        sid = await env.create(observer)  # FAST: a 2 s heartbeat timeout
        await env.sessions.submit(sid, Message("c1", "hello"))
        await env.wait_for(
            observer, sid, has_event(lambda p: type(p).__name__ == "MessageEnd" and p.role == "assistant")
        )
        await asyncio.sleep(1.5)  # the close has started, on this worker
    finally:
        worker.kill()  # SIGKILL in the middle of closing the run
        await worker.wait()

    killed = time.monotonic()
    async with session_worker(env.client, await env.state(), task_queue=env.queue):
        await env.wait_for(observer, sid, run_ended(), timeout=40)
        took = time.monotonic() - killed
        await env.sessions.submit(sid, Message("c2", "and again"))
        await env.wait_for(observer, sid, run_ended(2), timeout=30)
    # Heartbeat timeout (2 s), then placement (2 s) and adoption: seconds, not 2 minutes.
    assert took < 20, f"the dead worker's close held the session for {took:.0f}s"
