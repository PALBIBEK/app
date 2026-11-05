"""A person's view of the agent's browser, and their hand on it, against a real headless
Chromium run as a sandbox runs it (``tests.support.browser``): the tab the agent left,
shown; a click, typing and keys reaching the page; and the agent's next call finding the
browser as the person left it, with nothing it reads changed."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from supervisor.hands.browser.live import Click, Key, LiveRefused, Scroll, Type, act, screen
from supervisor.tools.workspace import HostWorkspace
from tests.support.browser import BROWSERS_ENV, Call, needs_browser

pytestmark = [pytest.mark.asyncio, needs_browser]

PAGE = (
    "<title>Checkout</title>"
    "<body style='margin:0'>"
    "<button id=go style='position:absolute;left:20px;top:20px;width:120px;height:40px'"
    " onclick=\"document.title='Paid'\">Pay</button>"
    "<input id=code style='position:absolute;left:20px;top:80px;width:200px;height:30px'>"
    "<div style='height:3000px'></div></body>"
)


@pytest.fixture
def live(tmp_path: Path) -> tuple[HostWorkspace, dict[str, str]]:
    """The same workspace and browser home the ``call`` fixture uses."""
    return HostWorkspace(tmp_path / "ws"), {"GITHI_BROWSER_HOME": str(tmp_path / "browser-home"), **BROWSERS_ENV}


async def test_with_no_browser_open_there_is_nothing_to_show_and_none_is_started(
    call: Call, live: tuple[HostWorkspace, dict[str, str]], tmp_path: Path
) -> None:
    workspace, env = live

    with pytest.raises(LiveRefused, match="no browser is open"):
        await screen(workspace, env)

    assert not list((tmp_path / "browser-home").glob("*/daemon.pid")), "looking started a browser"


async def test_a_person_sees_the_tab_the_agent_left_and_acts_on_it(call: Call, live: tuple[HostWorkspace, dict[str, str]]) -> None:
    workspace, env = live
    await call(f"# Opening the checkout\npage.set_content({PAGE!r})")

    seen = await screen(workspace, env)
    paid = await act(workspace, env, Click(kind="click", x=80, y=40))
    await act(workspace, env, Click(kind="click", x=120, y=95))
    await act(workspace, env, Type(kind="type", text="123456"))
    await act(workspace, env, Key(kind="key", key="Shift+Tab"))
    scrolled = await act(workspace, env, Scroll(kind="scroll", x=100, y=100, dy=600))
    after = await call("print(page.title(), js('document.querySelector(\"#code\").value'), js('window.scrollY'))")

    assert seen.title == "Checkout" and seen.width > 0 and seen.height > 0, seen
    assert base64.b64decode(seen.image)[:3] == b"\xff\xd8\xff", "not a JPEG"
    assert paid.title == "Paid", "the click did not reach the page"
    assert scrolled.image != paid.image
    assert after["output"] == "Paid 123456 600", "the agent's next call finds the page as the person left it"
    assert "browser_started" not in after, "a look must not hide a fresh browser from the agent"


async def test_a_key_the_live_view_does_not_take_is_refused(call: Call, live: tuple[HostWorkspace, dict[str, str]]) -> None:
    workspace, env = live
    await call(f"page.set_content({PAGE!r})")

    with pytest.raises(LiveRefused, match="not an input the live view takes"):
        await act(workspace, env, Key(kind="key", key="Notakey"))
