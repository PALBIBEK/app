"""Githi runtime — factory registration + AgentLoop offline turn (WP-A1/A2)."""

from __future__ import annotations

import asyncio
from pathlib import Path

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
from supervisor.agent.factory import create_runtime, known_runtime_names
from supervisor.agent.runtimes.githi.runtime import GithiRuntime


def test_githi_in_known_runtime_names() -> None:
    assert "githi" in known_runtime_names()


def test_create_runtime_githi() -> None:
    rt = create_runtime("githi", model="anthropic/claude-sonnet-4")
    assert isinstance(rt, GithiRuntime)


@pytest.mark.asyncio
async def test_githi_offline_turn_event_order(tmp_path: Path) -> None:
    (tmp_path / "note.md").write_text("# hi\n", encoding="utf-8")
    rt = create_runtime("githi", model="test")
    await rt.start(None, str(tmp_path))

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

    # Tool pair shares id; probe saw workspace file.
    # Re-run capture for payloads:
    rt2 = create_runtime("githi")
    await rt2.start(None, str(tmp_path))
    payloads: list[object] = []

    async def consume2() -> None:
        async for env in rt2.events():
            payloads.append(env.payload)
            if isinstance(env.payload, RunEnd):
                return

    c2 = asyncio.create_task(consume2())
    await asyncio.sleep(0)
    await rt2.send("probe", command_id="c2")
    await asyncio.wait_for(c2, timeout=2)

    start = next(p for p in payloads if isinstance(p, ToolStart))
    end = next(p for p in payloads if isinstance(p, ToolEnd))
    assert start.tool_call_id == end.tool_call_id
    assert start.tool_name == "workspace_probe"
    assert end.is_error is False
    assert "note.md" in end.result["entries"]

    state = await rt2.get_state()
    assert state.settled is True
    assert state.message_count == 2

    await rt.stop()
    await rt2.stop()


@pytest.mark.asyncio
async def test_githi_satisfies_agent_runtime_surface() -> None:
    rt = create_runtime("githi")
    for name in (
        "start",
        "send",
        "abort",
        "get_state",
        "get_history",
        "events",
        "stop",
        "get_capabilities",
    ):
        assert hasattr(rt, name), f"missing {name}"

    await rt.start(None, None)
    caps = await rt.get_capabilities()
    assert caps.protocol_version == "1"
    assert caps.supports_abort is True
    assert "githi" in caps.extras
    await rt.stop()


@pytest.mark.asyncio
async def test_githi_abort_mid_turn(tmp_path: Path) -> None:
    rt = create_runtime("githi")
    await rt.start(None, str(tmp_path))
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
    await rt.send("x", command_id="c1")
    await asyncio.wait_for(consumer, timeout=2)
    aborted = [
        e
        for e in envs
        if isinstance(e.payload, TurnEnd) and e.payload.stop_reason == "aborted"
    ]
    assert len(aborted) == 1
    await rt.stop()
