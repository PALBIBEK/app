"""pi's steering, made durable: a message sent while a run works joins that run at its
next step; the round's calls not yet run give way to it.

Against a real Temporal server, with a scripted gateway and a slow tool to steer during.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from supervisor.agent.events import MessageEnd, ToolEnd, ToolStart
from supervisor.temporal.contracts import Message
from supervisor.temporal.steps import run_id_for
from supervisor.temporal.worker import session_worker
from tests.support.githi_env import LS, WRITE, Env, event_types, has_event, payloads, run_ended
from tests.support.scripted import ScriptedGateway, slow_tool

pytestmark = [pytest.mark.asyncio(loop_scope="session"), pytest.mark.usefixtures("memory_service_url")]


def _user_texts(events: list) -> list[str]:  # type: ignore[type-arg]
    return [m.content[0]["text"] for m in payloads(events, MessageEnd) if m.role == "user"]


async def test_a_steering_message_joins_the_working_run(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    gateway = model(LS, WRITE)
    state = await env.state()
    with slow_tool("ls", 3):
        async with session_worker(env.client, state, task_queue=env.queue):
            sid = await env.create(state)
            await env.sessions.submit(sid, Message("c1", "list, then write"))
            await env.wait_for(state, sid, has_event(lambda p: isinstance(p, ToolStart)))
            accepted = await env.sessions.submit(sid, Message("c2", "actually, stop and look at this", steer=True))
            events = await env.wait_for(state, sid, run_ended())

    assert accepted.steered and accepted.run_id == run_id_for(sid, "c1")
    assert event_types(events).count("RunStart") == 1, "the message joined the run, it did not start one"
    assert _user_texts(events) == ["list, then write", "actually, stop and look at this"]
    first_write = next(e for e in payloads(events, ToolEnd) if e.tool_name == "write")
    assert first_write.result["code"] == "SKIPPED_FOR_USER_MESSAGE"  # gave way to the user
    # The model's next request read the message, after the round it interrupted.
    asked = next(s for s in gateway.seen if any(m.content == "actually, stop and look at this" for m in s))
    assert asked[-1].role == "user"
    steer_seq = next(e.seq for e in events if isinstance(e.payload, MessageEnd) and e.payload.role == "user" and e.seq > 1)
    assert steer_seq > max(e.seq for e in events if isinstance(e.payload, ToolEnd) and e.payload.tool_name == "ls")


async def test_a_steering_message_to_an_idle_session_runs_on_its_own(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    model()
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state)
        accepted = await env.sessions.submit(sid, Message("c1", "hello", steer=True))
        events = await env.wait_for(state, sid, run_ended())
    assert not accepted.steered
    assert _user_texts(events) == ["hello"]
