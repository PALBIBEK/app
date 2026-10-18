"""Githi runtime — factory registration + AgentLoop offline turn (WP-A1/A2)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from supervisor.agent.events import (
    RunEnd,
    ToolStart,
    TurnEnd,
)
from supervisor.agent.factory import create_runtime, known_runtime_names
from supervisor.agent.runtimes.githi.runtime import GithiRuntime


def test_githi_in_known_runtime_names() -> None:
    assert "githi" in known_runtime_names()


def test_create_runtime_githi() -> None:
    rt = create_runtime("githi", model="anthropic/claude-sonnet-4")
    assert isinstance(rt, GithiRuntime)




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
