"""RED → GREEN: Echo run stamps W3C trace_id on every envelope."""

from __future__ import annotations

import asyncio
import re

import pytest

from supervisor.agent.events import RunEnd
from supervisor.agent.runtimes.echo.runtime import EchoRuntime

_TRACE = re.compile(r"^[0-9a-f]{32}$")


@pytest.mark.asyncio
async def test_echo_run_propagates_trace_id() -> None:
    rt = EchoRuntime()
    await rt.start(None, None)
    envs = []

    async def consume() -> None:
        async for env in rt.events():
            envs.append(env)
            if isinstance(env.payload, RunEnd):
                return

    consumer = asyncio.create_task(consume())
    await asyncio.sleep(0)
    await rt.send("hello", command_id="c1")
    await asyncio.wait_for(consumer, timeout=2)
    assert envs
    tid = envs[0].trace_id
    assert tid is not None and _TRACE.match(tid)
    assert all(e.trace_id == tid for e in envs)
    await rt.stop()


@pytest.mark.asyncio
async def test_echo_honors_seeded_traceparent() -> None:
    rt = EchoRuntime()
    await rt.start(None, None)
    inbound = "d" * 32
    rt.seed_trace_id(inbound)
    envs = []

    async def consume() -> None:
        async for env in rt.events():
            envs.append(env)
            if isinstance(env.payload, RunEnd):
                return

    consumer = asyncio.create_task(consume())
    await asyncio.sleep(0)
    await rt.send("hi", command_id="c2")
    await asyncio.wait_for(consumer, timeout=2)
    assert all(e.trace_id == inbound for e in envs)
    await rt.stop()
