"""Sandbox lifecycle in running sessions (real Temporal, scripted model, local sandboxes).

``with_run``: the sandbox is built as the run begins, alongside the first model call, and the
first tool uses it. Idle: a sandbox left unused is saved and stopped; the session goes on and
its next tool call gets a fresh sandbox with the files restored.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import pytest

from supervisor.agent.events import HandProvisioned, HandStopped, ToolEnd
from supervisor.hands.lifecycle import SandboxLifecycle
from supervisor.seams.providers.storage.memory_object_storage import MemoryObjectStorage
from supervisor.seams.providers.workspace.memory_workspace_store import MemoryWorkspaceStore
from supervisor.temporal.contracts import Message
from supervisor.temporal.worker import session_worker
from tests.support.githi_env import WRITE, Env, payloads, run_ended
from tests.support.scripted import ScriptedGateway

pytestmark = pytest.mark.asyncio(loop_scope="session")

READ = ("read", {"file_path": "notes.txt"})


def _store() -> MemoryWorkspaceStore:
    return MemoryWorkspaceStore(objects=MemoryObjectStorage())


async def test_with_run_the_sandbox_is_built_as_the_run_begins_and_the_tool_uses_it(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    model(WRITE)
    state = await env.state()
    state.manager.sandbox_lifecycle = SandboxLifecycle(start="with_run")
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state)
        await env.sessions.submit(sid, Message("c1", "write a note"))
        events = await env.wait_for(state, sid, run_ended())
    (up,) = payloads(events, HandProvisioned)
    assert up.cause == "prewarm", "built before the tool asked; the tool shared it"
    assert payloads(events, ToolEnd)[0].result["ok"] is True
    assert len(state.manager._provider.create_calls) == 1  # type: ignore[union-attr]


async def test_on_first_tool_a_run_without_tools_builds_nothing(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    model()
    state = await env.state()
    state.manager.sandbox_lifecycle = SandboxLifecycle(start="on_first_tool")
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state)
        await env.sessions.submit(sid, Message("c1", "hello"))
        events = await env.wait_for(state, sid, run_ended())
    assert payloads(events, HandProvisioned) == []


async def test_an_idle_sandbox_is_saved_stopped_and_restored_on_the_next_tool(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    gateway = model(WRITE)
    state = await env.state(workspace_store=_store())
    state.manager.sandbox_lifecycle = SandboxLifecycle(start="on_first_tool", idle_stop_s=60)
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state)
        await env.sessions.submit(sid, Message("c1", "write a note"))
        await env.wait_for(state, sid, run_ended())
        first = state.manager._hands.sandbox(sid)
        assert first is not None
        assert await state.manager.stop_idle_sandboxes() == [], "not idle long enough yet"

        stopped = await state.manager.stop_idle_sandboxes(now=time.monotonic() + 120)
        assert stopped == [sid] and state.manager._hands.sandbox(sid) is None
        assert first.root.exists() is False  # type: ignore[attr-defined]  # really gone

        gateway.calls = [READ]
        await env.sessions.submit(sid, Message("c2", "read it back"))
        events = await env.wait_for(state, sid, run_ended(2))
    (stop,) = payloads(events, HandStopped)
    assert (stop.hand_id, stop.reason) == (first.id, "idle") and stop.idle_s >= 60
    assert [u.cause for u in payloads(events, HandProvisioned)] == ["first_use", "resumed"]
    read = payloads(events, ToolEnd)[-1]
    assert read.result == {"ok": True, "payload": "hello", "error": None, "code": None}, "restored"
    assert await state.manager.session_status(sid) == "active"
