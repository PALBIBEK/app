"""The browser code mode's runner: executes one ``browser_exec`` call inside the sandbox.

Shipped into the sandbox as source and run with the sandbox's own Python, so it imports
nothing from the supervisor: the standard library and Playwright only. It reads the
model's code on stdin and runs it with a Playwright ``page`` (and ``context``,
``browser``) already connected to the sandbox's browser, beside helpers in the shape
Hermes's browser-harness offers (``new_tab``, ``goto_url``, ``js``, ``cdp``, ...).

The browser outlives the call: ``daemon.py``, started once per browser name in a session
of its own and without the call's run tag, owns it (and a CDP connection with an event
buffer), so neither the call ending nor a stop of the call reaches it. Each call asks the
daemon for the browser's endpoint and connects to it over CDP; the model's code runs here,
in the call, where a stop can end it.

What the code prints is the result. On stderr, after anything the code wrote there, one
line starting with ``META_MARK`` carries what the tool reports besides: the page the call
ended on, whether a fresh browser had to be started, the call's error, and what the
browser's tabs reported since the previous call (dialogs, console errors, page errors).
"""

from __future__ import annotations

import contextlib
import functools
import importlib.util
import json
import os
import socket
import subprocess
import sys
import time
import traceback
from pathlib import Path
from types import ModuleType
from typing import Any

META_MARK = "\x1egithi-browser "
"""Starts the stderr line the tool reads back (an ASCII record separator, then a tag)."""
WORKSPACE = Path(os.environ.get("BROWSER_WORKSPACE") or ".githi/browser").resolve()
"""In the session's workspace, so it is saved with the session: batch files, helpers."""
ACTION_TIMEOUT_MS = 5_000
"""How long an action waits for its element: a locator that names nothing fails in
seconds, not Playwright's 30 (the code can pass ``timeout=`` for a slow element)."""
NAVIGATION_TIMEOUT_MS = 30_000
REPORTED = ("dialog", "console", "pageerror")
"""Events a result lists, of those the daemon keeps (navigations and requests are not)."""
MAX_REPORTED = 20
INTERNAL = ("chrome://", "chrome-untrusted://", "devtools://", "chrome-extension://")


def _sibling(name: str) -> ModuleType:
    """A module of the runtime beside this file (``daemon.py``, ``snapshot.py``), loaded by
    path: the runtime is a folder of scripts, not a package."""
    spec = importlib.util.spec_from_file_location(f"githi_browser_{name}", Path(__file__).with_name(f"{name}.py"))
    assert spec is not None and spec.loader is not None
    module = sys.modules[spec.name] = importlib.util.module_from_spec(spec)  # dataclasses look it up
    spec.loader.exec_module(module)
    return module


DAEMON = _sibling("daemon")
"""Its paths and constants are the ones the daemon serves on."""
SNAPSHOT = _sibling("snapshot")


def ask(op: str, **fields: Any) -> dict[str, Any]:
    """One request to the daemon; its answer. Raises OSError when none is serving."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(10)
        conn.connect(str(DAEMON.SOCKET))
        conn.sendall(json.dumps({"op": op, **fields}).encode() + b"\n")
        reply = conn.makefile("rb").readline()
    answer = json.loads(reply)
    if "error" in answer:
        raise RuntimeError(f"browser daemon: {answer['error']}")
    return dict(answer)


def _hello() -> dict[str, Any]:
    """The daemon's greeting (the browser's endpoint and generation), starting it if none
    answers: detached, untagged, so the call's end or a stop leaves it running."""
    if why := os.environ.get("GITHI_BROWSER_ERROR"):
        raise RuntimeError(why)  # the session's browser provider could not serve this call
    with contextlib.suppress(OSError):
        hello = ask("hello")
        if hello["endpoint"] == os.environ.get("GITHI_BROWSER_CDP", hello["endpoint"]):
            return hello
        ask("shutdown")  # a daemon on another remote browser: start one on this one
        time.sleep(0.2)
    env = {k: v for k, v in os.environ.items() if k != DAEMON.RUN_TAG_VAR}
    subprocess.Popen(
        [sys.executable, str(Path(__file__).with_name("daemon.py"))],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env=env, start_new_session=True,
    )
    deadline = time.monotonic() + DAEMON.START_TIMEOUT_S + 5
    while time.monotonic() < deadline:
        time.sleep(0.1)
        with contextlib.suppress(OSError):
            return ask("hello")
    log = DAEMON.HOME / "daemon.log"
    raise RuntimeError(log.read_text().strip().splitlines()[-1] if log.exists() else "the browser daemon did not start")


class Session:
    """This call's view of the browser: the page code acts on, and how it got there."""

    def __init__(self, browser: Any, hello: dict[str, Any]) -> None:
        self.browser = browser
        self.context = browser.contexts[0] if browser.contexts else browser.new_context()
        self.context.set_default_timeout(ACTION_TIMEOUT_MS)
        self.context.set_default_navigation_timeout(NAVIGATION_TIMEOUT_MS)
        seen = _state_file("seen-generation")
        self.started = not seen.exists() or seen.read_text() != str(hello["generation"])
        seen.write_text(str(hello["generation"]))
        for page in self.context.pages:
            _prepare(page)
        self.context.on("page", _prepare)
        self.page = self._current()

    def _current(self) -> Any:
        """The tab the previous call ended on, else the last real tab, else a new one."""
        pages = [p for p in self.context.pages if not p.url.startswith(INTERNAL)]
        saved = _state_file("tab").read_text() if _state_file("tab").exists() else ""
        for page in pages:
            if _target_id(page) == saved:
                return page
        return pages[-1] if pages else self.context.new_page()

    def remember(self) -> None:
        with contextlib.suppress(Exception):
            _state_file("tab").write_text(_target_id(self.page))

    def reported(self) -> list[dict[str, Any]]:
        """What the tabs reported since the previous call ended, oldest first."""
        cursor = _state_file("seq")
        since = int(cursor.read_text()) if cursor.exists() else 0
        answer = ask("events", since=since)
        cursor.write_text(str(answer["seq"]))
        events = [
            {k: v for k, v in e.items() if k not in ("seq", "at")} for e in answer["events"] if e["kind"] in REPORTED
        ]
        return events[-MAX_REPORTED:]


def _prepare(page: Any) -> None:
    """Every tab code may act on: dialogs left to the daemon (a page without a listener
    here would have Playwright dismiss them first, whatever the call asked), and
    ``wait_for_timeout`` the quiet wait (``snapshot.quiet_wait``)."""
    page.on("dialog", _leave_to_daemon)
    page.wait_for_timeout = functools.partial(SNAPSHOT.quiet_wait, page)


def _leave_to_daemon(_dialog: Any) -> None:
    """A dialog listener that leaves the answer to the daemon."""


def _state_file(name: str) -> Path:
    return Path(DAEMON.HOME) / name


def _target_id(page: Any) -> str:
    cdp = page.context.new_cdp_session(page)
    try:
        return str(cdp.send("Target.getTargetInfo")["targetInfo"]["targetId"])
    finally:
        cdp.detach()


class Helpers:
    """The helpers code runs with, in the shape Hermes's browser-harness offers, on the
    call's ``Session``; ``scope()`` is the namespace the code runs in."""

    NAMES = (
        "new_tab", "goto_url", "wait_for_load", "page_info", "js", "cdp", "fill_input", "click_at_xy",
        "capture_screenshot", "list_tabs", "switch_tab", "answer_dialogs", "wait_for_network_idle",
        "browser_events", "ensure_real_tab", "snapshot", "act", "settle",
    )

    def __init__(self, session: Session) -> None:
        self.session = session
        self.names: dict[str, Any] = {}
        self.snapshots = SNAPSHOT.Snapshots(Path(DAEMON.HOME), self.settle)

    def scope(self) -> dict[str, Any]:
        """Playwright's objects, the helpers, and what the agent saved in agent_helpers.py."""
        self.names = {
            "__name__": "__main__",
            "page": self.session.page,
            "context": self.session.context,
            "browser": self.session.browser,
            "WORKSPACE": str(WORKSPACE),
            **{name: getattr(self, name) for name in self.NAMES},
        }
        _load_agent_helpers(self.names)
        return self.names

    def _use(self, page: Any) -> Any:
        """Make ``page`` the one code acts on, under the name it reads too."""
        self.session.page = self.names["page"] = page
        return page

    def new_tab(self, url: str = "about:blank") -> Any:
        """Open ``url`` in a new tab and make it ``page``."""
        page = self._use(self.session.context.new_page())
        if url != "about:blank":
            page.goto(url, wait_until="domcontentloaded")
        return page

    def goto_url(self, url: str) -> Any:
        return self.session.page.goto(url, wait_until="domcontentloaded")

    def wait_for_load(self, timeout: float = 15.0) -> bool:
        try:
            self.session.page.wait_for_load_state("load", timeout=timeout * 1000)
        except Exception:  # noqa: BLE001 — a slow page is an answer, not a failure
            return False
        return True

    def page_info(self) -> dict[str, Any]:
        return dict(
            self.session.page.evaluate(
                "() => ({url: location.href, title: document.title, w: innerWidth, h: innerHeight,"
                " sx: scrollX, sy: scrollY, pw: document.documentElement.scrollWidth,"
                " ph: document.documentElement.scrollHeight})"
            )
        )

    def js(self, expression: str) -> Any:
        """Evaluate a JavaScript expression (or a function) in the page; its value."""
        return self.session.page.evaluate(expression)

    def cdp(self, method: str, **params: Any) -> Any:
        """Send one raw CDP command for the current page."""
        client = self.session.context.new_cdp_session(self.session.page)
        try:
            return client.send(method, params)
        finally:
            client.detach()

    def fill_input(self, selector: str, text: str) -> None:
        self.session.page.fill(selector, text)

    def click_at_xy(self, x: float, y: float) -> None:
        self.session.page.mouse.click(x, y)

    def capture_screenshot(self, path: str | None = None, full: bool = False) -> str:
        """Save a screenshot (in the workspace by default) and print its path."""
        if path is None:
            shots = WORKSPACE / "screenshots"
            shots.mkdir(parents=True, exist_ok=True)
            path = str(shots / f"shot-{time.strftime('%Y%m%d-%H%M%S')}-{time.monotonic_ns() % 10**6}.png")
        self.session.page.screenshot(path=path, full_page=full)
        print(path)
        return path

    def list_tabs(self) -> list[dict[str, str]]:
        return [{"id": _target_id(p), "url": p.url, "title": p.title()} for p in self.session.context.pages]

    def switch_tab(self, tab_id: str) -> Any:
        for candidate in self.session.context.pages:
            if _target_id(candidate) == tab_id:
                candidate.bring_to_front()
                return self._use(candidate)
        raise ValueError(f"no tab {tab_id!r}; list_tabs() shows the open ones")

    def answer_dialogs(self, action: str = "accept", text: str | None = None) -> None:
        """How the rest of this call answers native dialogs: "accept" (with ``text`` for a
        prompt) or "dismiss" (the default). Every dialog is listed in the result either way."""
        if action not in ("accept", "dismiss"):
            raise ValueError('answer_dialogs takes "accept" or "dismiss"')
        ask("dialogs", action=action, text=text)

    def wait_for_network_idle(self, idle_ms: int = 500, timeout: float = 10.0) -> bool:
        """Wait until the current tab has no request in flight and none began for
        ``idle_ms``; whether it got there in ``timeout`` seconds.

        Quiet is counted from the wait's start at the earliest: a request the code has just
        made may not have reached the daemon yet, so idle is never declared sooner."""
        tab = _target_id(self.session.page)
        started = time.monotonic()
        while (waited := time.monotonic() - started) < timeout:
            state = ask("network", tab=tab)
            quiet_ms = min(waited * 1000, state["quiet_ms"] if state["quiet_ms"] is not None else float("inf"))
            if state["inflight"] == 0 and quiet_ms >= idle_ms:
                return True
            time.sleep(0.1)
        return False

    def browser_events(self, since: int = 0) -> list[dict[str, Any]]:
        """Every event the daemon kept (dialogs, console errors, page errors, navigations),
        after the numbered one ``since``."""
        return list(ask("events", since=since)["events"])

    def snapshot(self, contains: str | None = None, max_lines: int = SNAPSHOT.DEFAULT_MAX_LINES) -> str:
        """The current tab's elements and the structure around them, each with an ID for
        ``act``; ``contains`` keeps the lines that match, with their groups."""
        return str(self.snapshots.snapshot(self.session.page, _target_id(self.session.page), contains=contains, max_lines=max_lines))

    def act(self, *steps: tuple[Any, ...]) -> None:
        """Act by the IDs of the tab's latest snapshot: ("click", "0-42"), ("fill", "0-7",
        "Ada"), ("select", "0-9", "Tech"), ("check", "0-3"), ("press", "0-7", "Enter");
        then settle. A stale ID is refused, and says what to do."""
        self.snapshots.act(self.session.page, _target_id(self.session.page), steps)

    def settle(self, page: Any = None, timeout: float = 5.0) -> None:
        """Wait for what the last action started to finish: a navigation, its requests,
        then a quiet DOM (at most ``timeout`` seconds)."""
        target = page or self.session.page
        SNAPSHOT.settle(target, lambda left: self.wait_for_network_idle(idle_ms=300, timeout=left), timeout)

    def ensure_real_tab(self) -> Any:
        """Make ``page`` a real, open tab again (after it closed, or landed on an internal one)."""
        return self._use(self.session._current())


def _load_agent_helpers(scope: dict[str, Any]) -> None:
    """Run the workspace's ``agent_helpers.py`` in the call's own scope, so what the agent
    saved there is in every call and itself sees ``page`` and the helpers."""
    source = WORKSPACE / "agent_helpers.py"
    if source.exists():
        exec(compile(source.read_text(), str(source), "exec"), scope)  # noqa: S102 — the agent's own file


def _meta(**fields: Any) -> None:
    sys.stderr.write(META_MARK + json.dumps(fields, default=str) + "\n")


def main() -> int:
    code = sys.stdin.read()
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    Path(DAEMON.HOME).mkdir(parents=True, exist_ok=True)
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        try:
            hello = _hello()
            session = Session(playwright.chromium.connect_over_cdp(hello["endpoint"]), hello)
        except Exception as exc:  # noqa: BLE001 — reported to the model as the call's error
            _meta(error=f"browser unavailable: {exc}", started=False)
            return 2
        failed = None
        try:
            exec(compile(code, "<browser_exec>", "exec"), Helpers(session).scope())  # noqa: S102 — code mode
        except Exception:  # noqa: BLE001 — the code's own error, shown as Python shows it
            failed = _traceback()
        finally:
            with contextlib.suppress(Exception):
                ask("dialogs", action="dismiss")  # the next call answers as it asks
        session.remember()
        url = ""
        with contextlib.suppress(Exception):
            url = session.page.url
        events: list[dict[str, Any]] = []
        with contextlib.suppress(Exception):
            events = session.reported()
        _meta(started=session.started, url=url, error=failed, events=events)
    return 1 if failed else 0


def _traceback() -> str:
    """The code's traceback, without the runner's frames or Playwright's internals; for
    code that does not parse, Python's own pointer at the line."""
    kind, value, tb = sys.exc_info()
    if kind is not None and issubclass(kind, SyntaxError):
        return "".join(traceback.format_exception_only(kind, value))
    frames = traceback.extract_tb(tb)
    own = [f for f in frames if f.filename != __file__ and f"{os.sep}playwright{os.sep}" not in f.filename]
    lines = traceback.format_list(own or frames[-1:]) + traceback.format_exception_only(kind, value)
    return "Traceback (most recent call last):\n" + "".join(lines)


if __name__ == "__main__":
    sys.exit(main())
