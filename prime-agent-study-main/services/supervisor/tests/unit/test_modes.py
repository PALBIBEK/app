"""Sticky vs. stateless-per-turn mode tests."""

from __future__ import annotations

import asyncio

import pytest

from supervisor.agent.events import RunEnd
from tests.support.waits import counted, journalled, until


@pytest.mark.asyncio
async def test_stateless_removes_runtime_after_run_end(session_env) -> None:
    m = session_env["manager"]
    registry = session_env["registry"]
    sid = await m.create(tenant_id="t1", harness_version="kg-v1", runtime_name="echo")
    q = m.live_queue(sid)
    await m.send(sid, "hello", command_id="c1")
    for _ in range(50):
        env = await asyncio.wait_for(q.get(), timeout=1)
        if isinstance(env.payload, RunEnd):
            break

    async def runtime() -> object:
        return registry.get(sid)

    # A stateless session stops its runtime once the turn has ended.
    await until(runtime, lambda r: r is None)
    await m.terminate(sid)


@pytest.mark.asyncio
async def test_stateless_resume_produces_same_event_types(session_env) -> None:
    m = session_env["manager"]
    reader = session_env["reader"]
    sid = await m.create(tenant_id="t1", harness_version="kg-v1", runtime_name="echo")
    q = m.live_queue(sid)
    await m.send(sid, "first", command_id="c1")
    for _ in range(50):
        env = await asyncio.wait_for(q.get(), timeout=1)
        if isinstance(env.payload, RunEnd):
            break
    first = [type(e.payload).__name__ for e in await journalled(reader, sid, counted("RunEnd"))]

    # Second turn after cold resume (runtime was removed).
    await m.send(sid, "second", command_id="c2")
    q = m.live_queue(sid)
    for _ in range(50):
        try:
            env = await asyncio.wait_for(q.get(), timeout=1)
        except TimeoutError:
            break
        if isinstance(env.payload, RunEnd):
            break
    all_events = [
        type(e.payload).__name__ for e in await journalled(reader, sid, counted("RunEnd", 2))
    ]
    second = all_events[len(first) :]
    assert first == second
    await m.terminate(sid)
