"""A command killed when its run is stopped: it and everything it started, at once."""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import pytest

from supervisor.agent.runtimes.githi.render import tool_result_failed, tool_result_text
from supervisor.sandbox.providers.local_process import LocalProcessSandbox
from supervisor.tools.workspace import HostWorkspace, SandboxWorkspace, Workspace


def _workspaces(root: Path) -> dict[str, Workspace]:
    return {
        "host": HostWorkspace(root),
        "sandbox": SandboxWorkspace(LocalProcessSandbox(root, "sb-stop")),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["host", "sandbox"])
async def test_a_stop_kills_the_command_and_what_it_started(kind: str, tmp_path: Path) -> None:
    ws = _workspaces(tmp_path)[kind]
    stop = threading.Event()
    marker, child = tmp_path / "late", tmp_path / "child-late"
    # The command and a background child it starts both write after 2 s, unless killed.
    command = f"(sleep 2; touch {child}) & sleep 2; touch {marker}; wait"
    threading.Timer(0.3, stop.set).start()
    started = time.monotonic()
    result = await ws.run(command, timeout_s=30, stop=stop)
    took = time.monotonic() - started

    assert result.stopped and not result.timed_out
    assert took < 1.5, f"the stop took {took:.1f}s"
    await asyncio.sleep(2.5)
    assert not marker.exists(), "the stopped command went on"
    assert not child.exists(), "a child of the stopped command went on"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["host", "sandbox"])
async def test_a_command_that_is_not_stopped_runs_as_before(kind: str, tmp_path: Path) -> None:
    ws = _workspaces(tmp_path)[kind]
    result = await ws.run("echo hi", timeout_s=10, stop=threading.Event())
    assert (result.exit_code, result.stdout.strip(), result.stopped) == (0, "hi", False)
    timed = await ws.run("sleep 5", timeout_s=0.5, stop=threading.Event())
    assert timed.timed_out and not timed.stopped


def test_a_stopped_command_reads_as_stopped_and_failed() -> None:
    result = {"ok": True, "payload": {"exit_code": -1, "output": "", "stdout": "", "stderr": "", "stopped": True}}
    assert tool_result_failed(result)
    assert "the run was stopped while it ran" in tool_result_text(result, "bash")
