"""githi's loop as pi's: what the model is told, request by request."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from supervisor.agent.events import ProjectContext
from supervisor.temporal.contracts import Message
from supervisor.temporal.worker import session_worker
from tests.support.githi_env import Env, payloads, run_ended
from tests.support.scripted import ScriptedGateway

pytestmark = [pytest.mark.asyncio(loop_scope="session"), pytest.mark.usefixtures("memory_service_url")]

AGENTS = "Run `make test` before you finish. Never touch vendor/.\n"


async def test_the_projects_agents_md_is_in_the_system_prompt(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    gateway = model(("write", {"path": "AGENTS.md", "content": AGENTS}))
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state)
        await env.sessions.submit(sid, Message("c1", "set the project up"))
        first = await env.wait_for(state, sid, run_ended())
        gateway.calls = []
        await env.sessions.submit(sid, Message("c2", "what are the rules here?"))
        events = await env.wait_for(state, sid, run_ended(2))

    assert not payloads(first, ProjectContext), "the first run began before the file existed"
    (read,) = payloads(events, ProjectContext)
    assert (read.path, read.text) == ("AGENTS.md", AGENTS)
    system = gateway.seen[-1][0].content
    assert '<project_instructions path="AGENTS.md">\nRun `make test` before you finish.' in system
    assert system.index("<project_context>") < system.index("<cwd>")  # pi's order


async def test_a_failed_tool_result_reaches_the_provider_flagged_as_an_error(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    gateway = model(("read", {"path": "missing.txt"}), ("ls", {"path": "."}))
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await env.create(state)
        await env.sessions.submit(sid, Message("c1", "look"))
        await env.wait_for(state, sid, run_ended())

    results = {m.tool_call_id: m for m in gateway.seen[-1] if m.role == "tool"}
    assert results["call-0-0"].is_error and "missing.txt" in results["call-0-0"].content
    assert not results["call-0-1"].is_error
