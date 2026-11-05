"""The browser daemon: what outlives a ``browser_exec`` call, against a real Chromium.

One daemon per browser name holds the browser, a CDP connection with an event buffer,
and the dialog answers; each call is a fresh process that asks it. So a call sees what
the tabs did while no call was running, a network-idle wait counts requests begun before
it, and a daemon or browser that died is noticed and replaced."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest

from tests.support.browser import Call, Site, kill, needs_browser

pytestmark = [pytest.mark.asyncio, needs_browser]


def _daemon_pid(tmp_path: Path) -> int:
    return int((tmp_path / "browser-home" / "default" / "daemon.pid").read_text())


def _alive(pid: int) -> bool:
    return Path(f"/proc/{pid}").exists()


async def test_what_a_tab_did_between_calls_reaches_the_next_result(call: Call) -> None:
    """A page keeps running after the call that opened it ends: its late errors and its
    dialog (answered, never left blocking the page) are in the next call's result."""
    await call(
        "page.set_content('<title>late</title><script>"
        "setTimeout(() => { console.error(\"late failure\"); alert(\"Saved!\"); }, 600)"
        "</script>')"
    )
    await asyncio.sleep(1.2)  # no call is running when the page reports

    later = await call("print(page.title())")

    kinds = {e["kind"]: e for e in later["events"]}
    assert later["output"] == "late", "the dialog was left blocking the page"
    assert kinds["console"]["text"] == "late failure"
    assert {kinds["dialog"][k] for k in ("type", "message", "answer")} == {"alert", "Saved!", "dismiss"}


async def test_each_event_is_reported_once(call: Call) -> None:
    await call("page.set_content('<script>console.error(\"once\")</script>')")

    again = await call("print('nothing new')")

    assert "events" not in again, again.get("events")


async def test_the_network_idle_wait_counts_a_request_still_in_flight(call: Call, site: Site) -> None:
    site.pages["/slow"] = ("ok", 1.5)
    slow_server = site.url("")
    waited = await call(
        f"page.goto({site.url('/page')!r})\n"
        # Started, not awaited: js() would wait for a returned promise.
        f"js(\"(() => {{ fetch('{slow_server}/slow').then(r => r.text()).then(t => document.title = t) }})()\")\n"
        "import time; started = time.monotonic()\n"
        "idle = wait_for_network_idle(idle_ms=300)\n"
        "print(idle, round(time.monotonic() - started, 1) >= 1.2, page.title())"
    )

    assert waited["output"] == "True True ok", waited


async def test_a_killed_daemon_is_restarted_and_finds_the_same_browser(call: Call, tmp_path: Path) -> None:
    await call("page.set_content('<title>kept</title>')")
    first = _daemon_pid(tmp_path)
    kill(first)

    later = await call("print(page.title())")

    assert later["output"] == "kept", "the browser went with its daemon"
    assert "browser_started" not in later, "a new daemon is not a new browser"
    assert _daemon_pid(tmp_path) != first


async def test_a_browser_that_died_is_reported_and_replaced(call: Call, tmp_path: Path) -> None:
    await call("page.set_content('<title>gone</title>')")
    port = (tmp_path / "browser-home" / "default" / "port").read_text()
    pid = await call(f"print(__import__('subprocess').run(['fuser', '{port}/tcp'], capture_output=True, text=True).stdout.split()[0])")
    kill(int(pid["output"]))
    await asyncio.sleep(0.5)

    later = await call("print(repr(page.title()))")

    assert later["browser_started"] is True, "the model would think its tabs and logins are still there"
    assert later["output"] == "''"


async def test_a_stop_leaves_the_daemon_running(call: Call, tmp_path: Path) -> None:
    await call("page.set_content('<title>Shop</title>')")
    daemon = _daemon_pid(tmp_path)
    stop = threading.Event()
    asyncio.get_running_loop().call_later(1.0, stop.set)

    await call("import time\ntime.sleep(30)", stop=stop)

    assert _alive(daemon), "the stop took the daemon with it"
    assert (await call("print(page.title())"))["output"] == "Shop"
