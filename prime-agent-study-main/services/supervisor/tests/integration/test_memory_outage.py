"""A memory-service outage costs long-term memory, not the conversation.

Found by running the stack: with the memory service stopped, new sessions failed
(503 after the create activity's retries) and every turn of an existing session
failed in recall. Long-term memory is an aid, so an outage now degrades: a turn
goes on without recall (and the model is told), and a session that only wanted
the implicit default notebook starts without one.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from supervisor.agent.events import ErrorEvent, MemoryInjected, RunStart, TurnEnd
from supervisor.memory.turn import RECALL_UNAVAILABLE
from supervisor.temporal.contracts import Message
from supervisor.temporal.sessions import SessionCommandError
from supervisor.temporal.worker import session_worker
from tests.support.githi_env import LS, Env, payloads, run_ended
from tests.support.notebooks import served_memory_service
from tests.support.scripted import ScriptedGateway

pytestmark = pytest.mark.asyncio(loop_scope="session")

DOWN = "http://127.0.0.1:9"  # nothing listens on the discard port


async def test_turns_go_on_without_recall_when_the_memory_service_goes_down(
    env: Env, model: Callable[..., ScriptedGateway], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model(LS)
    with served_memory_service(f"sqlite+aiosqlite:///{tmp_path / 'memory.db'}") as url:
        monkeypatch.setenv("MEMORY_SERVICE_URL", url)
        state = await env.state()
        async with session_worker(env.client, state, task_queue=env.queue):
            sid = await env.create(state)
            await env.sessions.submit(sid, Message("c1", "list the files"))
            await env.wait_for(state, sid, run_ended())
    # The memory service is gone now; the session and its worker are not.
    async with session_worker(env.client, state, task_queue=env.queue):
        await env.sessions.submit(sid, Message("c2", "list them again"))
        events = await env.wait_for(state, sid, run_ended(2), timeout=60)
    second = events[[i for i, e in enumerate(events) if isinstance(e.payload, RunStart)][-1]:]
    assert not payloads(second, ErrorEvent), payloads(second, ErrorEvent)
    assert payloads(second, TurnEnd)[-1].stop_reason == "stop"
    assert [m.text for m in payloads(second, MemoryInjected)] == [RECALL_UNAVAILABLE]


async def test_a_session_starts_without_long_term_memory_when_the_service_is_down(
    env: Env, model: Callable[..., ScriptedGateway], monkeypatch: pytest.MonkeyPatch
) -> None:
    model(LS)
    monkeypatch.setenv("MEMORY_SERVICE_URL", DOWN)
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state)  # the implicit default notebook is skipped
        await env.sessions.submit(sid, Message("c1", "list the files"))
        events = await env.wait_for(state, sid, run_ended())
        assert payloads(events, TurnEnd)[-1].stop_reason == "stop"
        with pytest.raises(SessionCommandError):  # notebooks a client names are not dropped
            await env.create(state, memory={"write": "work", "read": ["work"]})

