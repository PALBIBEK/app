"""The browser code mode in a real githi session: a composition built from the seams
(the browser tool set, a sandbox profile naming its browser), a durable run on Temporal,
the model calling ``browser_exec``, and the browser running in the session's sandbox.

And the fail-closed rule: a profile asking for a network its provider cannot enforce is
refused when the session is made, before anything runs."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from supervisor.agent.events import ToolEnd
from supervisor.catalog import NetworkAccess, NetworkMode
from supervisor.temporal.contracts import Message
from supervisor.temporal.worker import session_worker
from tests.support.browser import browser_session, needs_browser
from tests.support.githi_env import Env, payloads, run_ended
from tests.support.scripted import ScriptedGateway

pytestmark = [pytest.mark.asyncio(loop_scope="session"), pytest.mark.usefixtures("memory_service_url"), needs_browser]


async def test_a_browser_agent_built_from_the_seams_browses_in_its_sandbox(
    env: Env, model: Callable[..., ScriptedGateway], default_browsers: Path
) -> None:
    code = "page.set_content('<title>from the sandbox</title><button>b</button>')\nprint(page.title())\nprint(snapshot())"
    model(("browser_exec", {"code": code}))
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await browser_session(env, state, browser="sandbox-chromium")
        await env.sessions.submit(sid, Message("c1", "look at the page"))
        events = await env.wait_for(state, sid, run_ended(), timeout=60)

    (end,) = [e for e in payloads(events, ToolEnd) if e.tool_name == "browser_exec"]
    result = end.result["payload"]
    assert not end.is_error and result["exit_code"] == 0, end.result
    assert result["output"].startswith("from the sandbox\n"), result
    assert "button: b" in result["output"]


async def test_a_screenshot_the_code_shows_reaches_the_model_as_an_image(
    env: Env, model: Callable[..., ScriptedGateway], default_browsers: Path
) -> None:
    code = "page.set_content('<h1 style=\"color:red\">Sale</h1>')\nshow_screenshot()\nprint('looked')"
    gateway = model(("browser_exec", {"code": code}))
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await browser_session(env, state, browser="sandbox-chromium")
        await env.sessions.submit(sid, Message("c1", "what colour is the heading?"))
        events = await env.wait_for(state, sid, run_ended(), timeout=60)

    (end,) = [e for e in payloads(events, ToolEnd) if e.tool_name == "browser_exec"]
    (shot,) = end.result["payload"]["images"]
    (seen,) = [m for chat in gateway.seen for m in chat if m.role == "tool"]
    assert seen.images == [shot], "the model was not shown the screenshot"
    assert shot["data"] not in seen.content and "looked" in seen.content, "the image is not its text"


async def test_a_profile_whose_network_its_provider_cannot_enforce_is_refused(env: Env) -> None:
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        with pytest.raises(Exception, match="cannot enforce network 'open'"):
            await browser_session(env, state, network=NetworkAccess(NetworkMode.OPEN))
