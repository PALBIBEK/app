"""Hands in running githi sessions, against a real Temporal server and a scripted model.

A sandbox is built only when a tool needs one; when it dies, the model reads a
reset notice, the next call gets a fresh sandbox restored from the checkpoint
taken while the model was thinking, and the session goes on.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable

import pytest
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Replayer

from supervisor.agent.events import HandCheckpointed, HandLost, HandProvisioned, ToolEnd
from supervisor.seams.providers.storage.memory_object_storage import MemoryObjectStorage
from supervisor.seams.providers.workspace.memory_workspace_store import MemoryWorkspaceStore
from supervisor.temporal.contracts import Message
from supervisor.temporal.job_workflow import SubagentJobWorkflow
from supervisor.temporal.worker import session_worker
from supervisor.temporal.workflow import AgentSessionWorkflow
from tests.support.githi_env import WRITE, Env, payloads, run_ended
from tests.support.scripted import ScriptedGateway
from tests.support.workflow_histories import replayable_histories

pytestmark = pytest.mark.asyncio(loop_scope="session")

READ = ("read", {"file_path": "notes.txt"})


def _store() -> MemoryWorkspaceStore:
    return MemoryWorkspaceStore(objects=MemoryObjectStorage())


async def test_a_turn_that_needs_no_sandbox_never_builds_one(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    model()  # the model answers without calling any tool
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state)
        await env.sessions.submit(sid, Message("c1", "hello"))
        events = await env.wait_for(state, sid, run_ended())
    provider = state.manager._provider
    assert provider.create_calls == []  # type: ignore[union-attr]
    assert payloads(events, HandProvisioned) == []
    assert state.manager._hands.recipe(sid) is not None, "the recipe was resolved at create"


async def test_a_dead_sandbox_is_replaced_and_its_files_come_back(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    gateway = model(WRITE)
    state = await env.state(workspace_store=_store())
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state)
        await env.sessions.submit(sid, Message("c1", "write a note"))
        await env.wait_for(state, sid, run_ended())
        first = state.manager._hands.sandbox(sid)
        assert first is not None
        shutil.rmtree(first.root)  # type: ignore[attr-defined]  # the sandbox dies

        # The read hits the dead sandbox; the edit (it changes files, so it runs alone,
        # after the read) finds the replacement holding the checkpoint's files. Two
        # reads would now run together, and both could meet the dead sandbox.
        gateway.calls = [READ, ("edit", {"file_path": "notes.txt", "old_text": "hello", "new_text": "hello!"})]
        await env.sessions.submit(sid, Message("c2", "read it back"))
        events = await env.wait_for(state, sid, run_ended(2))

    saved = payloads(events, HandCheckpointed)[0]  # (the edit is saved after it)
    assert (saved.hand_id, saved.files) == (first.id, 1), "saved while the model answered"
    (lost,) = payloads(events, HandLost)
    assert lost.hand_id == first.id
    ups = payloads(events, HandProvisioned)
    assert [u.cause for u in ups] == ["first_use", "replaced"]
    reset, again = payloads(events, ToolEnd)[1:]
    assert reset.result["code"] == "WORKSPACE_RESET"
    assert "restored as they were at the last checkpoint" in reset.result["error"]
    assert again.result["ok"] and again.result["payload"]["replacements"] == 1  # "hello" came back
    assert await state.manager.session_status(sid) == "active", "the session goes on"


async def test_hands_histories_replay_deterministically(env: Env) -> None:
    replayer = Replayer(
        workflows=[AgentSessionWorkflow, SubagentJobWorkflow],
        data_converter=pydantic_data_converter,
    )
    async for history in replayable_histories(env.client, "WorkflowType = 'AgentSession'"):
        result = await replayer.replay_workflow(history, raise_on_replay_failure=False)
        assert result.replay_failure is None, result.replay_failure
