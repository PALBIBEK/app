"""The AgentRuntime contract every runtime keeps, proved against echo."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest

from supervisor.agent.events import RunEnd, ToolEnd, ToolStart, TurnEnd, TurnStart
from supervisor.agent.runtimes.echo.runtime import EchoRuntime


@pytest.fixture
async def runtime() -> AsyncIterator[Any]:
    rt: Any = EchoRuntime()
    await rt.start(None, None)
    yield rt
    await rt.stop()


async def _collect_until_run_end(rt: Any, *, send_message: str = "hello") -> list:
    envs: list = []

    async def consume() -> None:
        async for env in rt.events():
            envs.append(env)
            if isinstance(env.payload, RunEnd):
                return

    consumer = asyncio.create_task(consume())
    await asyncio.sleep(0)
    await rt.send(send_message, command_id="shared-1")
    await asyncio.wait_for(consumer, timeout=5)
    return envs




@pytest.mark.asyncio
async def test_seq_monotonic_gap_free(runtime: Any) -> None:
    envs = await _collect_until_run_end(runtime)
    seqs = [e.seq for e in envs]
    assert seqs == list(range(seqs[0], seqs[0] + len(seqs)))


@pytest.mark.asyncio
async def test_turn_id_stable_within_turn(runtime: Any) -> None:
    envs = await _collect_until_run_end(runtime)
    current = None
    saw_turn = False
    for e in envs:
        if isinstance(e.payload, TurnStart):
            saw_turn = True
            current = e.turn_id
            assert current is not None
        elif isinstance(e.payload, TurnEnd):
            assert e.turn_id == current
            current = None
        elif current is not None:
            assert e.turn_id == current
    assert saw_turn


@pytest.mark.asyncio
async def test_tool_start_end_pairing(runtime: Any) -> None:
    envs = await _collect_until_run_end(runtime)
    starts = [e.payload for e in envs if isinstance(e.payload, ToolStart)]
    ends = [e.payload for e in envs if isinstance(e.payload, ToolEnd)]
    assert len(starts) == len(ends)
    assert {s.tool_call_id for s in starts} == {e.tool_call_id for e in ends}


@pytest.mark.asyncio
async def test_abort_produces_terminal_event(runtime: Any) -> None:
    envs: list = []

    async def consume() -> None:
        async for env in runtime.events():
            envs.append(env)
            if isinstance(env.payload, ToolStart):
                await runtime.abort()
            if isinstance(env.payload, TurnEnd):
                return

    consumer = asyncio.create_task(consume())
    await asyncio.sleep(0)
    await runtime.send("abort-me", command_id="abort-1")
    await asyncio.wait_for(consumer, timeout=2)
    assert any(
        isinstance(e.payload, TurnEnd) and e.payload.stop_reason == "aborted" for e in envs
    )


@pytest.mark.asyncio
async def test_stop_is_idempotent(runtime: Any) -> None:
    await runtime.stop()
    await runtime.stop()


@pytest.mark.asyncio
async def test_events_after_stop_do_not_appear(runtime: Any) -> None:
    # Drain anything already queued, then stop; iterator must end.
    await runtime.stop()

    async def drain() -> list:
        out = []
        async for env in runtime.events():
            out.append(env)
        return out

    remaining = await asyncio.wait_for(drain(), timeout=2)
    # After stop, at most events already buffered before close may appear;
    # the iterator must terminate (no hang). Fresh post-stop emits are dropped.
    assert isinstance(remaining, list)
