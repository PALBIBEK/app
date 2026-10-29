"""A worker that lost its session cannot write it (pi-temporal's fence).

The session is taken over while its worker is mid-tool (as when that worker froze past
the placement limit and another adopted the session). When the old worker wakes up, its
tool's result and its checkpoint describe a sandbox that no longer serves: both are
refused, so the call is answered as unknown and the new worker's checkpoints stand.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from supervisor.agent.events import HandCheckpointed, ToolEnd, ToolStart, TurnEnd
from supervisor.hands.checkpoint import checkpoint_scope
from supervisor.seams.providers.storage.memory_object_storage import MemoryObjectStorage
from supervisor.seams.providers.workspace.memory_workspace_store import MemoryWorkspaceStore
from supervisor.temporal.contracts import Message
from supervisor.temporal.worker import session_worker
from tests.support.githi_env import WRITE, Env, has_event, payloads, run_ended
from tests.support.scripted import ScriptedGateway, slow_tool

pytestmark = [pytest.mark.asyncio(loop_scope="session"), pytest.mark.usefixtures("memory_service_url")]


async def test_a_worker_that_lost_its_session_cannot_write_it(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    model(WRITE)
    store = MemoryWorkspaceStore(objects=MemoryObjectStorage())
    old = await env.state(workspace_store=store)
    new = await env.state(workspace_store=store)
    new.manager.holds.holder = "the-adopting-worker"
    with slow_tool("write", 2.0):
        async with session_worker(env.client, old, task_queue=env.queue):
            sid = await env.create(old)
            await env.sessions.submit(sid, Message("c1", "write a note"))
            await env.wait_for(old, sid, has_event(lambda p: isinstance(p, ToolStart)))
            await new.manager.holds.take(sid)  # adopted while the old worker's write runs
            events = await env.wait_for(old, sid, run_ended(), timeout=30)

    (end,) = payloads(events, ToolEnd)
    assert end.result["code"] == "TOOL_OUTCOME_UNKNOWN", "the old worker's result was recorded"
    assert not payloads(events, HandCheckpointed), "the old worker saved its sandbox"
    assert not await store.list_files(checkpoint_scope(sid))
    assert payloads(events, TurnEnd)[-1].stop_reason == "stop"  # the run goes on
