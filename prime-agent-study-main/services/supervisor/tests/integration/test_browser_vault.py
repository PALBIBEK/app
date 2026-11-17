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

from supervisor.agent.events import ApprovalRequired, ToolEnd
from supervisor.db.engine import open_db
from supervisor.main import create_app
from supervisor.temporal.contracts import Message
from supervisor.temporal.worker import session_worker
from supervisor.tools import default_registry
from supervisor.tools.builtin import browser_vault as vault_tool
from supervisor.tools.context import ToolContext
from supervisor.tools.workspace import HostWorkspace
from supervisor.vault import Keyring, Owner, Vault
from supervisor.vault.logins import BrowserLogins
from supervisor.vault.totp import normalize_otp_secret, totp_now
from tests.support.browser import BROWSERS_ENV, Site, browser_session, needs_browser, stop_browsers
from tests.support.githi_env import Env, has_event, payloads, run_ended
from tests.support.scripted import ScriptedGateway

PASSWORD = "S3cret-pass-2026!"
LOGIN_PAGE = f"""<title>Sign in</title>
<form id=f><label>Username <input id=user></label><label>Password <input id=pass type=password></label>
<button id=go>Sign in</button></form>
<script>document.getElementById('f').onsubmit = e => {{ e.preventDefault();
  const ok = document.getElementById('pass').value === {json.dumps(PASSWORD)};
  document.title = ok ? 'Signed in' : 'Wrong password';
  if (ok) document.body.innerHTML = '<h1>Welcome ' + document.getElementById('user').value + '</h1>'; }}</script>"""

Tool = Callable[..., Awaitable[dict[str, Any]]]
CARD = {"origins": [], "card_number": "4242424242424242", "cardholder_name": "Ada Lovelace", "exp_month": "4", "exp_year": "2031", "cvc": "731"}
OTP_SEED = "JBSWY3DPEHPK3PXP"


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
        await vault.put(
            Owner.tenant("t1"), f"login:{site.url('', host='localhost')}",
            json.dumps({"username": "grace", "password": PASSWORD, "otp": f"otpauth://totp/Shop:grace?secret={OTP_SEED}"}),
        )
        await vault.put(Owner.tenant("t1"), "card:Visa-4242", json.dumps({**CARD, "origins": [site.url("")]}))
        await vault.put(
            Owner.tenant("t1"), "address:Home",
            json.dumps({"origins": [site.url("")], "address_line1": "12 Analytical St", "city": "London", "postal_code": "N1 9GU", "country": "United Kingdom"}),
        )
        logins = BrowserLogins(vault).bind("t1")

        def context() -> ToolContext:
            return ToolContext(
                bound_workspace=HostWorkspace(tmp_path / "ws"),
                timeout_s=60,
                env={"GITHI_BROWSER_HOME": str(home), **BROWSERS_ENV},
                logins=logins,
            )

        async def run(name: str, **args: Any) -> dict[str, Any]:
            return dict(await default_registry()[name].run(args, context()))

        run.context = context  # type: ignore[attr-defined]
        yield run
    stop_browsers(home)


pytestmark = [needs_browser]


@pytest.mark.asyncio
async def test_a_saved_login_is_filled_and_submitted_without_being_shown(tools: Tool, site: Site) -> None:
    await tools("browser_exec", code=f"page.goto({site.url('/login')!r})")

    listed = await tools("browser_vault", action="list")
    filled = await tools("browser_vault", action="fill", login=site.url(""), username="#user", password="#pass", submit="#go")
    after = await tools("browser_exec", code="print(page.title(), '|', page.inner_text('h1'))")

    assert listed["items"][0] == {
        "handle": f"login:{site.url('')}", "kind": "login", "origin": site.url(""), "identifier": "ada", "two_factor": "the user is asked for the code",
    }
    assert PASSWORD not in json.dumps(listed) and "4242424242424242" not in json.dumps(listed) and OTP_SEED not in json.dumps(listed)
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

    assert answer["error"] == "no saved item 'https://nowhere.example.com': list shows the saved ones"
    assert f"login:{site.url('')}" in [i["handle"] for i in answer["items"]]


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



# Hermes's fill: the form found and filled in-page, by the login classifier ------------------

CHECKOUT = """<title>Pay</title><form>
<input autocomplete=cc-name id=name><input autocomplete=cc-number id=number>
<input autocomplete=cc-exp id=exp placeholder="MM / YY"><input name=cvv id=cvc>
<input autocomplete=address-line1 id=street><input name=city id=city><input name=zip id=zip>
<select name=country id=country><option value=fr>France</option><option value=gb>United Kingdom</option></select>
</form>"""
CODE_BOXES = "<title>Verify</title><form>" + "".join(
    f"<input name=d{i} autocomplete=one-time-code maxlength=1 inputmode=numeric>" for i in range(6)
) + "</form>"


@pytest.mark.asyncio
async def test_the_form_is_found_and_filled_without_naming_its_fields(tools: Tool, site: Site) -> None:
    await tools("browser_exec", code=f"page.goto({site.url('/login')!r})")

    filled = await tools("browser_vault", action="fill", handle=f"login:{site.url('')}")
    after = await tools("browser_exec", code="page.click('#go')\nprint(page.title(), '|', page.inner_text('h1'))")

    assert filled["success"] is True and filled["filled_fields"] == 2, filled
    assert filled["fields"] == ["current-password", "username"] and filled["origin"] == site.url("")
    assert PASSWORD not in json.dumps(filled)
    assert after["output"] == "Signed in | Welcome ada"


@pytest.mark.asyncio
async def test_a_fill_is_refused_unless_a_page_is_on_the_items_site(tools: Tool, site: Site) -> None:
    site.pages["/elsewhere"] = ("<title>elsewhere</title><input type=password id=pass>", 0.0)
    await tools("browser_exec", code=f"page.goto({site.url('/elsewhere', host='localhost')!r})")

    refused = await tools("browser_vault", action="fill", handle=f"login:{site.url('')}")
    typed = await tools("browser_exec", code="print(repr(page.input_value('#pass')))")

    assert refused["success"] is False and refused["error_type"] == "origin_mismatch", refused
    assert typed["output"] == "''"


@pytest.mark.asyncio
async def test_the_tab_holding_the_form_is_used_when_the_current_one_is_elsewhere(tools: Tool, site: Site) -> None:
    """Hermes's focus_page: the agent opened another tab after the login page."""
    await tools("browser_exec", code=f"page.goto({site.url('/login')!r})\nnew_tab({site.url('/news', host='localhost')!r})")

    filled = await tools("browser_vault", action="fill", handle=f"login:{site.url('')}")
    tab = await tools("browser_exec", code="print(page.title(), len(page.input_value('#pass')))")

    assert filled["success"] is True, filled
    assert tab["output"] == f"Sign in {len(PASSWORD)}", "the next call is on the tab that was filled"


@pytest.mark.asyncio
async def test_a_card_and_an_address_are_filled_into_a_checkout(tools: Tool, site: Site) -> None:
    site.pages["/pay"] = (CHECKOUT, 0.0)
    await tools("browser_exec", code=f"page.goto({site.url('/pay')!r})")

    card = await tools("browser_vault", action="fill", handle="card:Visa-4242")
    address = await tools("browser_vault", action="fill", handle="address:Home")
    read = await tools("browser_exec", code="import json; print(json.dumps({k: page.input_value('#' + k) for k in ('name', 'number', 'exp', 'cvc', 'street', 'city', 'zip', 'country')}))")

    assert card["success"] is True and card["fields"] == ["cc-csc", "cc-exp", "cc-name", "cc-number"], card
    assert "4242424242424242" not in json.dumps(card) and "731" not in json.dumps(card)
    assert address["success"] is True and address["fields"] == ["address-level2", "address-line1", "country-name", "postal-code"], address
    assert json.loads(read["output"]) == {
        "name": "Ada Lovelace", "number": "4242424242424242", "exp": "04/31", "cvc": "731",
        "street": "12 Analytical St", "city": "London", "zip": "N1 9GU", "country": "gb",
    }


@pytest.mark.asyncio
async def test_a_saved_authenticator_key_types_its_code_one_digit_per_box(tools: Tool, site: Site) -> None:
    site.pages["/2fa"] = (CODE_BOXES, 0.0)
    await tools("browser_exec", code=f"page.goto({site.url('/2fa', host='localhost')!r})")
    seed = normalize_otp_secret(OTP_SEED)
    before = totp_now(seed)

    typed = await tools("browser_vault", action="enter_code", handle=f"login:{site.url('', host='localhost')}")
    boxes = await tools("browser_exec", code="print(''.join(page.input_value(f'[name=d{i}]') for i in range(6)))")

    assert typed["success"] is True and typed["filled_fields"] == 6 and typed["source"] == "authenticator key", typed
    assert boxes["output"] in {before, totp_now(seed)}, "the code is the saved key's"
    assert boxes["output"] not in json.dumps(typed), "the code reached the result"


@pytest.mark.asyncio
async def test_without_a_key_the_code_is_the_users_and_a_page_without_a_code_field_says_so(tools: Tool, site: Site) -> None:
    site.pages["/2fa"] = (CODE_BOXES, 0.0)
    await tools("browser_exec", code=f"page.goto({site.url('/2fa')!r})")

    asked = await tools("browser_vault", action="enter_code", handle=f"login:{site.url('')}")
    given = await tools("browser_vault", action="enter_code", handle=f"login:{site.url('')}", code="246 810")
    boxes = await tools("browser_exec", code=f"print(''.join(page.input_value(f'[name=d{{i}}]') for i in range(6)))\npage.goto({site.url('/login')!r})")
    no_field = await tools("browser_vault", action="enter_code", handle=f"login:{site.url('')}", code="246810")

    assert asked["error_type"] == "code_from_user" and "live view" in asked["error"], asked
    assert given["success"] is True and given["source"] == "user", given
    assert boxes["output"] == "246810"
    assert no_field["error_type"] == "no_code_field" and "passkey" in no_field["error"]


@pytest.mark.asyncio
async def test_a_page_that_left_the_site_between_inspecting_and_filling_gets_nothing(tools: Tool, site: Site) -> None:
    """The fill script asserts the origin itself, right before it writes (Hermes's TOCTOU check)."""
    site.pages["/elsewhere"] = ("<title>elsewhere</title><input type=password id=pass>", 0.0)
    await tools("browser_exec", code=f"page.goto({site.url('/login')!r})")
    ctx = tools.context()  # type: ignore[attr-defined]
    args = vault_tool.BrowserVaultArgs(action="fill", handle=f"login:{site.url('')}")
    inspected = await vault_tool._step(args, ctx, "vault-inspect", {"kind": "login", "origins": [site.url("")], "nonce": "n1"})
    await tools("browser_exec", code=f"page.goto({site.url('/elsewhere', host='localhost')!r})")

    written = await vault_tool._write(args, ctx, inspected, [{"index": 1, "token": "current-password", "value": PASSWORD}])
    typed = await tools("browser_exec", code="print(repr(page.input_value('#pass')))")

    assert written["success"] is False and written["error_type"] == "origin_changed", written
    assert typed["output"] == "''"


@pytest.mark.asyncio
async def test_with_nothing_saved_for_a_site_the_user_is_sent_to_save_it(tools: Tool, site: Site) -> None:
    site.pages["/signin"] = ("<title>other</title><input type=password>", 0.0)
    await tools("browser_exec", code=f"page.goto({site.url('/signin', host='localhost')!r})")

    answer = await tools("browser_vault", action="save_login")

    assert answer["error_type"] == "save_in_app" and answer["origin"] == site.url("", host="localhost"), answer
    assert "Saved logins" in answer["error"]


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.usefixtures("memory_service_url")
async def test_in_a_session_a_card_waits_for_a_person_and_a_refusal_fills_nothing(
    env: Env, model: Callable[..., ScriptedGateway], default_browsers: Path, site: Site
) -> None:
    """Hermes asks the user before a card goes into a page; here the session asks a person
    (an approval), whatever the harness's own approvals say, and a denied fill never runs."""
    site.pages["/pay"] = (CHECKOUT, 0.0)
    model(
        ("browser_exec", {"code": f"page.goto({site.url('/pay')!r})"}),
        ("browser_vault", {"action": "fill", "handle": "card:Visa-4242"}),
        ("browser_exec", {"code": "print(repr(page.input_value('#number')))"}),
        one_at_a_time=True,
    )
    state = await env.state()
    api = TestClient(create_app(state=state), headers={"X-Tenant-Id": "t1"})
    assert api.put("/v1/secrets/card:Visa-4242", json={"value": json.dumps({**CARD, "origins": [site.url("")]})}).status_code == 200

    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await browser_session(env, state, browser="sandbox-chromium")
        await env.sessions.submit(sid, Message("c1", "pay"))
        asked = await env.wait_for(state, sid, has_event(lambda p: isinstance(p, ApprovalRequired)), timeout=90)
        (approval,) = payloads(asked, ApprovalRequired)
        await env.sessions.respond_approval(sid, approval.approval_id, "deny")
        events = await env.wait_for(state, sid, run_ended(), timeout=90)

    ends = [e for e in payloads(events, ToolEnd) if e.tool_name.startswith("browser_")]
    assert "Visa-4242" in approval.prompt and "never reach the agent" in approval.prompt
    assert ends[1].tool_name == "browser_vault" and ends[1].result["code"] == "APPROVAL_DENIED"
    assert ends[2].result["payload"]["output"] == "''", "the card was filled although the person refused"
