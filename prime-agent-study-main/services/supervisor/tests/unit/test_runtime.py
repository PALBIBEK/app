"""HarnessRuntime lifecycle tests against a fake sandbox replaying the fixture."""

from __future__ import annotations

import asyncio

import pytest

from supervisor.agent.events import RunEnd, RunStart, ToolEnd, ToolStart, TurnEnd, TurnStart
from supervisor.agent.runtimes.harness.runtime import HarnessRuntime
from tests.support.replay import ReplaySandbox


@pytest.mark.asyncio
async def test_start_captures_session_id() -> None:
    sandbox = ReplaySandbox()
    rt = HarnessRuntime(provider="openrouter", model="anthropic/claude-sonnet-4")
    await rt.start(sandbox, None)
    assert rt.session_id == "019ff5c6-8cd1-7168-baad-6b90e9d0373d"
    await rt.stop()


@pytest.mark.asyncio
async def test_events_fixture_order_contiguous_seq_and_turn_ids() -> None:
    sandbox = ReplaySandbox()
    rt = HarnessRuntime(provider="openrouter", model="anthropic/claude-sonnet-4")
    await rt.start(sandbox, None)
    await rt.send("read a.txt", command_id="prompt-1")

    envs = []
    async for env in rt.events():
        envs.append(env)
        if isinstance(env.payload, RunEnd):
            break

    assert isinstance(envs[0].payload, RunStart)
    seqs = [e.seq for e in envs]
    assert seqs == list(range(len(seqs)))

    current: str | None = None
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
async def test_send_resolves_on_matching_response_id() -> None:
    sandbox = ReplaySandbox()
    rt = HarnessRuntime(provider="openrouter", model="anthropic/claude-sonnet-4")
    await rt.start(sandbox, None)
    await asyncio.wait_for(rt.send("hi", command_id="prompt-match"), timeout=2)
    await rt.stop()


@pytest.mark.asyncio
async def test_unmatched_response_id_does_not_deadlock() -> None:
    sandbox = ReplaySandbox()
    rt = HarnessRuntime(provider="openrouter", model="anthropic/claude-sonnet-4")
    await rt.start(sandbox, None)
    sandbox.handle._emit({"id": "orphan", "type": "response", "command": "prompt", "success": True})
    await asyncio.wait_for(rt.send("hi", command_id="prompt-real"), timeout=2)
    await rt.stop()


@pytest.mark.asyncio
async def test_process_die_mid_stream_ends_iterator() -> None:
    sandbox = ReplaySandbox()
    rt = HarnessRuntime(provider="openrouter", model="anthropic/claude-sonnet-4")
    await rt.start(sandbox, None)
    sandbox.handle._emit({"type": "agent_start"})
    sandbox.handle._stdout.close()

    collected = []

    async def drain() -> None:
        async for env in rt.events():
            collected.append(env)

    await asyncio.wait_for(drain(), timeout=2)
    assert len(collected) >= 1
    assert isinstance(collected[0].payload, RunStart)
    await rt.stop()


@pytest.mark.asyncio
async def test_stop_kills_process() -> None:
    sandbox = ReplaySandbox()
    rt = HarnessRuntime(provider="openrouter", model="anthropic/claude-sonnet-4")
    await rt.start(sandbox, None)
    await rt.stop()
    assert sandbox.handle._killed is True


@pytest.mark.asyncio
async def test_tool_start_end_pairing_via_events() -> None:
    sandbox = ReplaySandbox()
    rt = HarnessRuntime(provider="openrouter", model="anthropic/claude-sonnet-4")
    await rt.start(sandbox, None)
    await rt.send("read a.txt", command_id="p1")
    starts: list[ToolStart] = []
    ends: list[ToolEnd] = []
    async for env in rt.events():
        if isinstance(env.payload, ToolStart):
            starts.append(env.payload)
        elif isinstance(env.payload, ToolEnd):
            ends.append(env.payload)
        if isinstance(env.payload, RunEnd):
            break
    assert len(starts) == len(ends) == 1
    assert starts[0].tool_call_id == ends[0].tool_call_id
    await rt.stop()
