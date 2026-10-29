"""A tool call has a time limit (pi-temporal's tool timeout): past it the call is stopped,
the model is told, and the run goes on; a hung tool no longer holds a worker slot for the
run's whole limit (24 h)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from supervisor.agent.events import ToolEnd, TurnEnd
from supervisor.temporal import steps
from supervisor.temporal.contracts import Message
from supervisor.temporal.worker import session_worker
from tests.integration.test_stop_stops_tools import write_runs_a_command
from tests.support.githi_env import WRITE, Env, payloads, run_ended
from tests.support.scripted import ScriptedGateway, slow_tool

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.mark.parametrize("tool", ["command", "unstoppable"])
async def test_a_tool_call_past_its_limit_is_stopped_and_the_run_goes_on(
    env: Env, model: Callable[..., ScriptedGateway], monkeypatch: pytest.MonkeyPatch, tmp_path: Path, tool: str
) -> None:
    monkeypatch.setattr(steps, "TOOL_CALL_LIMIT_S", 2.0)
    model(WRITE)  # one write, then the model answers
    marker = tmp_path / "late"
    state = await env.state()
    slow = write_runs_a_command(marker) if tool == "command" else slow_tool("write", 60)
    with slow:
        async with session_worker(env.client, state, task_queue=env.queue):
            sid = await env.create(state)
            started = time.monotonic()
            await env.sessions.submit(sid, Message("c1", "write it"))
            events = await env.wait_for(state, sid, run_ended(), timeout=30)
            took = time.monotonic() - started
            await asyncio.sleep(4 if tool == "command" else 0)

    (end,) = payloads(events, ToolEnd)
    assert end.is_error and end.result["code"] == steps.TOOL_TIMEOUT, end.result
    assert "time limit (2 s)" in end.result["error"]
    assert payloads(events, TurnEnd)[-1].stop_reason == "stop"  # the model answered after it
    assert took < 15, f"the limit (2 s) took {took:.1f}s to end the call"
    if tool == "command":
        assert not marker.exists(), "the command went on past the call's limit"
