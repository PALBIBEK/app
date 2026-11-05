"""The live view and takeover over HTTP, in a real githi session on Temporal: the agent's
browser shown to a person, their click reaching it once the agent's turn is over, and
refused while the agent works in it."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Callable
from pathlib import Path

import pytest
from httpx import AsyncClient, Response

from supervisor.api.browser import AGENT_WORKING
from supervisor.temporal.contracts import Message
from supervisor.temporal.worker import session_worker
from tests.support.api import supervisor_api
from tests.support.browser import browser_session, needs_browser
from tests.support.githi_env import Env, run_ended
from tests.support.scripted import ScriptedGateway

pytestmark = [pytest.mark.asyncio(loop_scope="session"), pytest.mark.usefixtures("memory_service_url"), needs_browser]

PAGE = "<title>Verify</title><button style='position:absolute;left:0;top:0;width:200px;height:60px' onclick=\"document.title='Verified'\">I am human</button>"


async def _until_shown(api: AsyncClient, sid: str, timeout: float = 60) -> Response:
    """The browser's screen, once there is one to show (the agent's first step opens it)."""
    async with asyncio.timeout(timeout):
        while (shown := await api.get(f"/v1/sessions/{sid}/browser/screen")).status_code != 200:
            await asyncio.sleep(0.25)
    return shown


async def test_a_person_watches_the_agents_browser_and_takes_over_when_its_turn_is_over(
    env: Env, model: Callable[..., ScriptedGateway], default_browsers: Path, tmp_path: Path
) -> None:
    release = tmp_path / "release"  # the step works until the test has looked
    code = (
        f"# Opening the check\npage.set_content({PAGE!r})\nimport os, time\n"
        f"deadline = time.time() + 90\nwhile not os.path.exists({str(release)!r}) and time.time() < deadline:\n    time.sleep(0.1)"
    )
    model(("browser_exec", {"code": code}))
    state = await env.state()
    state.workflows = env.sessions
    async with session_worker(env.client, state, task_queue=env.queue), supervisor_api(state) as api:
        sid = await browser_session(env, state, browser="sandbox-chromium")
        before = await api.get(f"/v1/sessions/{sid}/browser/screen")
        await env.sessions.submit(sid, Message("c1", "get past the check"))
        watched = await _until_shown(api, sid)  # the agent is working: its step waits for the release
        while_working = await api.post(f"/v1/sessions/{sid}/browser/input", json={"kind": "click", "x": 100, "y": 30})
        release.touch()
        await env.wait_for(state, sid, run_ended(), timeout=90)
        clicked = await api.post(f"/v1/sessions/{sid}/browser/input", json={"kind": "click", "x": 100, "y": 30})
        bad = await api.post(f"/v1/sessions/{sid}/browser/input", json={"kind": "drag", "x": 1})

    assert before.status_code == 409, before.text  # no sandbox yet, or no browser in it
    assert watched.json()["title"] == "Verify", "a person can watch while the agent works"
    assert base64.b64decode(watched.json()["image"])[:3] == b"\xff\xd8\xff"
    assert while_working.status_code == 409 and AGENT_WORKING in while_working.text
    assert clicked.status_code == 200 and clicked.json()["title"] == "Verified", clicked.text
    assert bad.status_code == 422
