"""Saved logins, typed into a real page and never shown to the model.

At the tool: ``browser_vault`` fills a login by CSS selector or snapshot ID and submits
it, only on the login's own origin, and its result names what it filled, not with what.
In a githi session: a login saved through the secrets API is filled for the model, and
when the model's code reads the password field back, the password is in no journal event
and in nothing the model was sent.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from supervisor.agent.events import ToolEnd
from supervisor.db.engine import open_db
from supervisor.main import create_app
from supervisor.temporal.contracts import Message
from supervisor.temporal.worker import session_worker
from supervisor.tools import default_registry
from supervisor.tools.context import ToolContext
from supervisor.tools.workspace import HostWorkspace
from supervisor.vault import Keyring, Owner, Vault
from supervisor.vault.logins import BrowserLogins
from tests.support.browser import BROWSERS_ENV, Site, browser_session, needs_browser, stop_browsers
from tests.support.githi_env import Env, payloads, run_ended
from tests.support.scripted import ScriptedGateway

PASSWORD = "S3cret-pass-2026!"
LOGIN_PAGE = f"""<title>Sign in</title>
<form id=f><label>User <input id=user></label><label>Password <input id=pass type=password></label>
<button id=go>Sign in</button></form>
<script>document.getElementById('f').onsubmit = e => {{ e.preventDefault();
  const ok = document.getElementById('pass').value === {json.dumps(PASSWORD)};
  document.title = ok ? 'Signed in' : 'Wrong password';
  if (ok) document.body.innerHTML = '<h1>Welcome ' + document.getElementById('user').value + '</h1>'; }}</script>"""

Tool = Callable[..., Awaitable[dict[str, Any]]]


@pytest.fixture
async def tools(tmp_path: Path, site: Site) -> AsyncIterator[Tool]:
    """``browser_exec`` and ``browser_vault`` on one workspace and browser, with a vault
    holding a login for ``site``."""
    site.pages["/login"] = (LOGIN_PAGE, 0.0)
    home = tmp_path / "browser-home"
    (tmp_path / "ws").mkdir()
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'v.db'}") as (_, factory):
        vault = Vault(factory, Keyring({"k1": b"k1".ljust(32, b".")}, active="k1"))
        await vault.put(Owner.tenant("t1"), f"login:{site.url('')}", json.dumps({"username": "ada", "password": PASSWORD}))
        logins = BrowserLogins(vault).bind("t1")

        async def run(name: str, **args: Any) -> dict[str, Any]:
            ctx = ToolContext(
                bound_workspace=HostWorkspace(tmp_path / "ws"),
                timeout_s=60,
                env={"GITHI_BROWSER_HOME": str(home), **BROWSERS_ENV},
                logins=logins,
            )
            return dict(await default_registry()[name].run(args, ctx))

        yield run
    stop_browsers(home)


pytestmark = [needs_browser]


@pytest.mark.asyncio
async def test_a_saved_login_is_filled_and_submitted_without_being_shown(tools: Tool, site: Site) -> None:
    await tools("browser_exec", code=f"page.goto({site.url('/login')!r})")

    listed = await tools("browser_vault", action="list")
    filled = await tools("browser_vault", action="fill", login=site.url(""), username="#user", password="#pass", submit="#go")
    after = await tools("browser_exec", code="print(page.title(), '|', page.inner_text('h1'))")

    assert listed == {"logins": [site.url("")]}
    assert filled.get("filled") == ["username", "password"] and filled.get("submitted") is True, filled
    assert PASSWORD not in json.dumps(filled)
    assert after["output"] == "Signed in | Welcome ada"


@pytest.mark.asyncio
async def test_fields_can_be_named_by_snapshot_ids(tools: Tool, site: Site) -> None:
    shown = await tools("browser_exec", code=f"page.goto({site.url('/login')!r})\nprint(snapshot())")
    out = shown["output"]

    filled = await tools(
        "browser_vault", action="fill", login=site.url(""),
        username=_id(out, "User"), password=_id(out, "Password"), submit=_id(out, "Sign in"),
    )
    after = await tools("browser_exec", code="print(page.title())")

    assert filled.get("filled") == ["username", "password"], filled
    assert after["output"] == "Signed in"


def _id(snapshot: str, text: str) -> str:
    """The ID on the first element line of ``snapshot`` that mentions ``text``."""
    line = next(line for line in snapshot.splitlines() if text in line and "[" in line)
    return line.split("[", 1)[1].split("]", 1)[0]


@pytest.mark.asyncio
async def test_a_login_is_not_typed_into_another_origin(tools: Tool, site: Site) -> None:
    """localhost and 127.0.0.1 are different sites to the browser, and so to the vault."""
    await tools("browser_exec", code=f"page.goto({site.url('/login', host='localhost')!r})")

    refused = await tools("browser_vault", action="fill", login=site.url(""), username="#user", password="#pass")
    typed = await tools("browser_exec", code="print(repr(page.input_value('#pass')))")

    assert "this login is for" in refused["error"] and "filled" not in refused
    assert typed["output"] == "''", "the password was typed into another site's page"


@pytest.mark.asyncio
async def test_an_unknown_login_says_which_there_are(tools: Tool, site: Site) -> None:
    answer = await tools("browser_vault", action="fill", login="https://nowhere.example.com", password="#pass")

    assert answer["error"] == "no saved login for https://nowhere.example.com"
    assert answer["logins"] == [site.url("")]


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.usefixtures("memory_service_url")
async def test_in_a_session_the_password_reaches_neither_the_journal_nor_the_model(
    env: Env, model: Callable[..., ScriptedGateway], default_browsers: Path, site: Site
) -> None:
    site.pages["/login"] = (LOGIN_PAGE, 0.0)
    origin = site.url("")
    read_back = "print('typed:', page.input_value('#pass'))"
    gateway = model(
        ("browser_exec", {"code": f"page.goto({site.url('/login')!r})"}),
        ("browser_vault", {"action": "fill", "login": origin, "username": "#user", "password": "#pass"}),
        ("browser_exec", {"code": read_back}),
        one_at_a_time=True,
    )
    state = await env.state()
    api = TestClient(create_app(state=state), headers={"X-Tenant-Id": "t1"})
    assert api.put(f"/v1/secrets/login:{origin}", json={"value": "ada:" + PASSWORD}).status_code == 422
    assert api.put(f"/v1/secrets/login:{origin}", json={"value": json.dumps({"username": "ada", "password": PASSWORD})}).status_code == 200

    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await browser_session(env, state, browser="sandbox-chromium")
        await env.sessions.submit(sid, Message("c1", "sign in"))
        events = await env.wait_for(state, sid, run_ended(), timeout=90)

    ends = [e for e in payloads(events, ToolEnd) if e.tool_name.startswith("browser_")]
    assert [e.tool_name for e in ends] == ["browser_exec", "browser_vault", "browser_exec"]
    assert ends[1].result["payload"]["filled"] == ["username", "password"], ends[1].result
    assert ends[2].result["payload"]["output"] == "typed: [redacted:login]", "the code read the field back"
    assert PASSWORD not in json.dumps([e.payload for e in events], default=repr), "the password is in the journal"
    prompts = json.dumps([[m.content for m in chat] for chat in gateway.seen], default=repr)
    assert PASSWORD not in prompts and "[redacted:login]" in prompts

