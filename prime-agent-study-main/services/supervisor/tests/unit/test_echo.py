"""Echo-specific behaviour (sandbox=None, abort, zero Usage)."""

from __future__ import annotations

import asyncio

import pytest

from supervisor.agent.events import (
    MessageEnd,
    RunEnd,
    RunStart,
    ToolEnd,
    ToolStart,
    TurnEnd,
    TurnStart,
)
from supervisor.agent.runtimes.echo.runtime import EchoRuntime


@pytest.mark.asyncio
async def test_canned_sequence_order() -> None:
    rt = EchoRuntime()
    await rt.start(None, None)
    types: list[type] = []

    async def consume() -> None:
        async for env in rt.events():
            types.append(type(env.payload))
            if isinstance(env.payload, RunEnd):
                return

    consumer = asyncio.create_task(consume())
    await asyncio.sleep(0)
    await rt.send("hello", command_id="c1")
    await asyncio.wait_for(consumer, timeout=2)
    assert types == [
        RunStart,
        TurnStart,
        ToolStart,
        ToolEnd,
        MessageEnd,
        TurnEnd,
        RunEnd,
    ]
    await rt.stop()


@pytest.mark.asyncio
async def test_seq_contiguous_from_zero() -> None:
    rt = EchoRuntime()
    await rt.start(None, None)
    seqs: list[int] = []

    async def consume() -> None:
        async for env in rt.events():
            seqs.append(env.seq)
            if isinstance(env.payload, RunEnd):
                return

    consumer = asyncio.create_task(consume())
    await asyncio.sleep(0)
    await rt.send("x", command_id="c1")
    await asyncio.wait_for(consumer, timeout=2)
    assert seqs == list(range(len(seqs)))
    await rt.stop()


@pytest.mark.asyncio
async def test_turn_id_stable_within_turn() -> None:
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
    await rt.send("x", command_id="c1")
    await asyncio.wait_for(consumer, timeout=2)

    current = None
    for e in envs:
        if isinstance(e.payload, TurnStart):
            current = e.turn_id
            assert current is not None
        elif isinstance(e.payload, TurnEnd):
            assert e.turn_id == current
            current = None
        elif current is not None:
            assert e.turn_id == current
    await rt.stop()


@pytest.mark.asyncio
async def test_tool_start_end_share_tool_call_id() -> None:
    rt = EchoRuntime()
    await rt.start(None, None)
    start = end = None

    async def consume() -> None:
        nonlocal start, end
        async for env in rt.events():
            if isinstance(env.payload, ToolStart):
                start = env.payload
            elif isinstance(env.payload, ToolEnd):
                end = env.payload
            if isinstance(env.payload, RunEnd):
                return

    consumer = asyncio.create_task(consume())
    await asyncio.sleep(0)
    await rt.send("ping", command_id="c1")
    await asyncio.wait_for(consumer, timeout=2)
    assert start is not None and end is not None
    assert start.tool_call_id == end.tool_call_id
    assert start.tool_name == "echo"
    await rt.stop()


@pytest.mark.asyncio
async def test_start_with_sandbox_none() -> None:
    rt = EchoRuntime()
    await rt.start(None, None)  # must not raise
    assert rt.session_id is not None
    state = await rt.get_state()
    assert state.settled is True
    await rt.stop()


@pytest.mark.asyncio
async def test_abort_mid_sequence_emits_aborted_turn_end() -> None:
    rt = EchoRuntime()
    await rt.start(None, None)
    envs = []

    async def consume() -> None:
        async for env in rt.events():
            envs.append(env)
            if isinstance(env.payload, ToolStart):
                await rt.abort()
            if isinstance(env.payload, TurnEnd) and env.payload.stop_reason == "aborted":
                return
            if isinstance(env.payload, RunEnd):
                return

    consumer = asyncio.create_task(consume())
    await asyncio.sleep(0)
    await rt.send("hi", command_id="c1")
    await asyncio.wait_for(consumer, timeout=2)
    aborted = [
        e
        for e in envs
        if isinstance(e.payload, TurnEnd) and e.payload.stop_reason == "aborted"
    ]
    assert len(aborted) == 1
    await rt.stop()


@pytest.mark.asyncio
async def test_get_state_settled_when_idle() -> None:
    rt = EchoRuntime()
    await rt.start(None, None)
    assert (await rt.get_state()).settled is True

    async def consume() -> None:
        async for env in rt.events():
            if isinstance(env.payload, RunEnd):
                return

    consumer = asyncio.create_task(consume())
    await asyncio.sleep(0)
    await rt.send("hi", command_id="c1")
    await asyncio.wait_for(consumer, timeout=2)
    assert (await rt.get_state()).settled is True
    await rt.stop()


@pytest.mark.asyncio
async def test_usage_is_all_zeros() -> None:
    rt = EchoRuntime()
    await rt.start(None, None)
    usage = None

    async def consume() -> None:
        nonlocal usage
        async for env in rt.events():
            if isinstance(env.payload, MessageEnd):
                usage = env.payload.usage
            if isinstance(env.payload, RunEnd):
                return

    consumer = asyncio.create_task(consume())
    await asyncio.sleep(0)
    await rt.send("hi", command_id="c1")
    await asyncio.wait_for(consumer, timeout=2)
    assert usage is not None
    assert usage.input_tokens == usage.output_tokens == 0
    assert usage.cache_read == usage.cache_write == 0
    assert usage.cost_total == 0.0
    await rt.stop()
