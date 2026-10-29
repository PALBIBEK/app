"""A stop stops the tool that runs: its command is killed, and its real result is recorded.

Before, a stop (the user's, or the run's hard deadline) cancelled the step that waited on
the tool, but the tool ran on: a write landed after the run had ended, and the model was told
the outcome was unknown though the call had finished. pi-temporal waits for a stopped tool
to stop and report.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from supervisor.agent.events import ErrorEvent, ToolEnd, ToolStart
from supervisor.seams.core.types import ToolResult
from supervisor.seams.providers.tools.registry_tools import RegistryToolProvider, _run_blocking
from supervisor.temporal.contracts import Message
from supervisor.temporal.worker import session_worker
from supervisor.tools.context import ToolContext
from tests.support.githi_env import WRITE, Env, has_event, payloads, run_ended
from tests.support.scripted import ScriptedGateway

pytestmark = pytest.mark.asyncio(loop_scope="session")


@contextmanager
def write_runs_a_command(marker: Path) -> Iterator[None]:
    """``write`` runs a real 5 s command in the session's workspace, as bash would."""
    original = RegistryToolProvider.invoke

    def invoke(self: RegistryToolProvider, ctx: Any, tool: str, args: dict[str, Any]) -> Any:
        if tool != "write":
            return original(self, ctx, tool, args)
        tc = ToolContext.from_invoke(ctx)
        done = _run_blocking(lambda: tc.workspace.run(f"sleep 5; touch {marker}", timeout_s=60, stop=tc.stop))
        payload = {"exit_code": done.exit_code, "output": "", "stdout": "", "stderr": ""}
        return ToolResult(ok=True, payload={**payload, **({"stopped": True} if done.stopped else {})})

    RegistryToolProvider.invoke = invoke  # type: ignore[method-assign]
    try:
        yield
    finally:
        RegistryToolProvider.invoke = original  # type: ignore[method-assign]


async def _check(env: Env, state: Any, sid: str, marker: Path) -> list[Any]:
    events = await env.wait_for(state, sid, run_ended(), timeout=30)
    await asyncio.sleep(6)  # past the command's own 5 s
    assert not marker.exists(), "the stopped command went on after the run ended"
    (end,) = payloads(events, ToolEnd)
    assert end.result["payload"]["stopped"] is True, end.result  # its real result, not "unknown"
    assert end.is_error
    return events


async def test_the_hard_deadline_kills_the_running_command(
    env: Env, model: Callable[..., ScriptedGateway], tmp_path: Path
) -> None:
    model(WRITE)
    marker = tmp_path / "late"
    state = await env.state()
    with write_runs_a_command(marker):
        async with session_worker(env.client, state, task_queue=env.queue):
            sid = await env.create(state, "githi-deadline")  # a 2 s hard deadline
            await env.sessions.submit(sid, Message("c1", "write it"))
            events = await _check(env, state, sid, marker)
    assert [e.error_class for e in payloads(events, ErrorEvent)] == ["RunDeadline"]


async def test_a_stop_kills_the_running_command_and_the_next_run_goes_on(
    env: Env, model: Callable[..., ScriptedGateway], tmp_path: Path
) -> None:
    model(WRITE)
    marker = tmp_path / "late"
    state = await env.state()
    with write_runs_a_command(marker):
        async with session_worker(env.client, state, task_queue=env.queue):
            sid = await env.create(state)
            await env.sessions.submit(sid, Message("c1", "write it"))
            await env.wait_for(state, sid, has_event(lambda p: isinstance(p, ToolStart)))
            await asyncio.sleep(0.5)
            stopped_at = time.monotonic()
            await env.sessions.stop_run(sid, "c1")
            await env.wait_for(state, sid, run_ended(), timeout=30)
            took = time.monotonic() - stopped_at
            await _check(env, state, sid, marker)
    # The tool hears the stop on its next heartbeat (every 2 s at most), kills its command
    # and records it; the close does not wait out its whole grace.
    assert took < 6, f"the stop took {took:.1f}s to end the run"
