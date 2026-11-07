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
browser's tabs reported since the previous call (dialogs, console errors, page errors),
and the screenshots the code asked to be shown with the result (``show_screenshot``).

``runner.py fill`` is the other way in, for ``browser_vault``: it types a saved login into
the current tab. The login arrives in ``FILL_ENV`` from the supervisor, never in code the
model wrote, and the answer says what was filled, never with what.

``runner.py dialog`` and ``runner.py cdp`` are ``browser_dialog``'s and ``browser_cdp``'s:
they answer a dialog the daemon's CDP supervisor holds, or send one raw command over its
connection, and never start a browser.

``runner.py live`` is the person's way in (``supervisor.hands.browser.live``): the current
tab as a JPEG, after their click, typing, key or scroll if ``LIVE_ENV`` carries one. It
never starts a browser, and leaves what a call reads (the tab it ended on, the browser's
generation, the event cursor) as it was.
"""

from __future__ import annotations

import base64
import contextlib
import functools
import importlib.util
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.parse import urlsplit

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
FILL_ENV = "GITHI_BROWSER_FILL"
"""``{"origin": …, "targets": {"username"|"password"|"submit": ID or selector}, "values": {…}}``."""
VAULT_ENV = "GITHI_BROWSER_VAULT"
"""``vault-inspect``: ``{"kind", "origins": [...], "nonce"}``; ``vault-write``: ``{"tab",
"expression"}`` (the fill script, which holds the values: it is never in code the model wrote)."""
DIALOG_ENV = "GITHI_BROWSER_DIALOG"
"""``{"action": "accept"|"dismiss", "text": …, "dialog_id": …}``: ``browser_dialog``'s answer."""
CDP_ENV = "GITHI_BROWSER_CDP_CALL"
"""``{"method": …, "params": {…}, "target_id": …, "frame_id": …, "timeout": …}``: ``browser_cdp``'s command."""
BLOCKED_AFTER_S = 1.0
"""How long a held dialog may block the page before the call ends to say so: the page's
JavaScript waits on it, so the code's next step on the page would wait for the call's timeout."""
NO_BROWSER = "no browser is open: the agent has not used one in this sandbox"
LIVE_ENV = "GITHI_BROWSER_LIVE"
"""``{"input": {"kind": "click"|"type"|"key"|"scroll", ...}}`` or ``{}`` (the screen only)."""
LIVE_QUALITY = 60
"""JPEG quality of the live view: refreshed every second or so, so small over sharp."""
LIVE_KEY = re.compile(r"^(?:(?:Control|Shift|Alt|Meta)\+)*(?:[A-Za-z0-9]|F\d{1,2}|Enter|Tab|Escape|Backspace|Delete|Space|"
                      r"Home|End|PageUp|PageDown|ArrowUp|ArrowDown|ArrowLeft|ArrowRight|Insert)$")
"""Keys a person may press in the live view, in Playwright's names, with modifiers."""
SNAPSHOT_ID = re.compile(r"\d+-\d+")
SHOWN_QUALITY = 70
"""JPEG quality of a screenshot shown to the model: legible text at a fraction of a PNG."""
MAX_SHOWN = 2
"""Screenshots one result shows, the latest: one is usually the page as it ended."""
_DEFAULT_PORTS = {"http": "80", "https": "443"}
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
FORMS = _sibling("vault_forms")


def ask(op: str, wait: float = 10.0, **fields: Any) -> dict[str, Any]:
    """One request to the daemon, answered in at most ``wait`` seconds. Raises OSError when
    none is serving, and RuntimeError with what the daemon refused."""
    answer = told(op, wait, **fields)
    if "error" in answer:
        raise RuntimeError(f"browser daemon: {answer['error']}")
    return answer


def told(op: str, wait: float = 10.0, **fields: Any) -> dict[str, Any]:
    """The daemon's answer to one request as it gave it, an error included. Raises OSError
    when none is serving."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(wait)
        conn.connect(str(DAEMON.SOCKET))
        conn.sendall(json.dumps({"op": op, **fields}).encode() + b"\n")
        reply = conn.makefile("rb").readline()
    return dict(json.loads(reply))


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

    def __init__(self, browser: Any, started: bool) -> None:
        self.browser = browser
        self.context = browser.contexts[0] if browser.contexts else browser.new_context()
        self.context.set_default_timeout(ACTION_TIMEOUT_MS)
        self.context.set_default_navigation_timeout(NAVIGATION_TIMEOUT_MS)
        self.started = started
        for page in self.context.pages:
            _prepare(page)
        self.context.on("page", _prepare)
        self.page = self._current()
        self.shown: list[str] = []
        """Screenshots to show with the result, as paths in the workspace folder."""

    def _current(self) -> Any:
        """The tab the previous call ended on, else the last real tab, else a new one."""
        return current_tab(self.context) or self.context.new_page()

    def remember(self) -> None:
        with contextlib.suppress(Exception):
            _state_file("tab").write_text(_target_id(self.page))

    def reported(self) -> list[dict[str, Any]]:
        return reported()


def started_afresh(hello: dict[str, Any]) -> bool:
    """Whether the browser is another than the one the previous call saw (its tabs and
    logins gone); the next call compares with this one."""
    seen = _state_file("seen-generation")
    started = not seen.exists() or seen.read_text() != str(hello["generation"])
    seen.write_text(str(hello["generation"]))
    return started


def reported() -> list[dict[str, Any]]:
    """What the tabs reported since the previous call ended, oldest first."""
    cursor = _state_file("seq")
    since = int(cursor.read_text()) if cursor.exists() else 0
    answer = ask("events", since=since)
    cursor.write_text(str(answer["seq"]))
    events = [{k: v for k, v in e.items() if k not in ("seq", "at")} for e in answer["events"] if e["kind"] in REPORTED]
    return _distinct(events)[-MAX_REPORTED:]


def current_tab(context: Any) -> Any | None:
    """The tab the last call ended on, else the last real tab; None if there is none."""
    pages = [p for p in context.pages if not p.url.startswith(INTERNAL)]
    saved = _state_file("tab").read_text() if _state_file("tab").exists() else ""
    for page in pages:
        if _target_id(page) == saved:
            return page
    return pages[-1] if pages else None


def _distinct(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """``events`` with each repeat folded into its first (``times``): a page logging one
    error per navigation says it once, not twenty times."""
    first: dict[str, dict[str, Any]] = {}
    for event in events:
        key = json.dumps(event, sort_keys=True, default=str)
        if key in first:
            first[key]["times"] = first[key].get("times", 1) + 1
        else:
            first[key] = dict(event)
    return list(first.values())


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
        "capture_screenshot", "show_screenshot", "list_tabs", "switch_tab", "answer_dialogs", "wait_for_network_idle",
        "browser_events", "ensure_real_tab", "snapshot", "act", "settle", "locate", "frames",
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
        path = path or _shot_path("shot", "png")
        self.session.page.screenshot(path=path, full_page=full)
        print(path)
        return path

    def show_screenshot(self, full: bool = False) -> str:
        """Screenshot the tab (its viewport, or the whole page) to be shown, as an image,
        with this call's result; its path in the workspace."""
        path = _shot_path("shown", "jpg")
        self.session.page.screenshot(path=path, type="jpeg", quality=SHOWN_QUALITY, full_page=full, scale="css")
        self.session.shown.append(path)
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
        prompt) or "dismiss". Otherwise a dialog is held for ``browser_dialog``, and the call
        ends to say so. Every dialog is listed in the result either way."""
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

    def frames(self) -> dict[str, Any]:
        """The page's frames as the CDP supervisor tracks them: the top frame and its iframes,
        each with its ``frame_id`` (an out-of-process one, an iframe of another site, can be
        reached with ``browser_cdp(frame_id=...)``)."""
        return dict(ask("frames")["frame_tree"])

    def browser_events(self, since: int = 0) -> list[dict[str, Any]]:
        """Every event the daemon kept (dialogs, console errors, page errors, navigations),
        after the numbered one ``since``."""
        return list(ask("events", since=since)["events"])

    def snapshot(self, contains: str | None = None, max_lines: int = SNAPSHOT.DEFAULT_MAX_LINES) -> str:
        """The current tab's elements and the structure around them, each with an ID for
        ``act``; ``contains`` keeps the lines that match, with their groups."""
        return str(self.snapshots.snapshot(self.session.page, _target_id(self.session.page), contains=contains, max_lines=max_lines))

    def locate(self, target: str) -> Any:
        """The element a snapshot ID ("0-42") or a CSS selector names, on the current tab."""
        page = self.session.page
        if SNAPSHOT_ID.fullmatch(target):
            return self.snapshots.locate(page, _target_id(page), target)
        return page.locator(target)

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


def _shot_path(kind: str, suffix: str) -> str:
    shots = WORKSPACE / "screenshots"
    shots.mkdir(parents=True, exist_ok=True)
    return str(shots / f"{kind}-{time.strftime('%Y%m%d-%H%M%S')}-{time.monotonic_ns() % 10**6}.{suffix}")


def _meta(**fields: Any) -> None:
    sys.stderr.write(META_MARK + json.dumps(fields, default=str) + "\n")


def origin(url: str) -> str:
    """``scheme://host[:port]``, lower-cased, without the scheme's default port."""
    parts = urlsplit(url)
    host, _, port = (parts.netloc.lower().rsplit("@", 1)[-1]).partition(":")
    return f"{parts.scheme.lower()}://{host}" + (f":{port}" if port and port != _DEFAULT_PORTS.get(parts.scheme.lower()) else "")


def fill() -> int:
    """Type a saved login into the current tab, if the tab is on the login's origin; print
    what was filled (``{"filled": [...], ...}`` or ``{"error": ...}``), never the values."""
    request = json.loads(os.environ.pop(FILL_ENV))
    Path(DAEMON.HOME).mkdir(parents=True, exist_ok=True)
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        try:
            hello = _hello()
            session = Session(playwright.chromium.connect_over_cdp(hello["endpoint"]), started_afresh(hello))
        except Exception as exc:  # noqa: BLE001 — reported to the model as the call's error
            print(json.dumps({"error": f"browser unavailable: {exc}"}))
            return 2
        on = origin(session.page.url)
        if on != request["origin"]:
            print(json.dumps({"error": f"the tab is on {on or session.page.url!r}, this login is for {request['origin']}: open its page first"}))
            return 1
        helpers = Helpers(session)
        filled: list[str] = []
        try:
            for field in ("username", "password"):
                if target := request["targets"].get(field):
                    helpers.locate(target).fill(request["values"][field])
                    filled.append(field)
            if submit := request["targets"].get("submit"):
                helpers.locate(submit).click()
                helpers.settle()
        except Exception as exc:  # noqa: BLE001 — Playwright's message names the target, not the value
            print(json.dumps({"error": f"{type(exc).__name__}: {str(exc).splitlines()[0]}", "filled": filled}))
            return 1
        session.remember()
        print(json.dumps({"filled": filled, "submitted": bool(submit), "url": session.page.url}))
    return 0


def live() -> int:
    """The current tab as the person sees it (``{"url", "title", "width", "height", "image"}``,
    the image a base64 JPEG), after their input if they gave one; ``{"error": ...}`` when no
    browser is open or the input is not one this takes."""
    request = json.loads(os.environ.pop(LIVE_ENV, "") or "{}")
    try:
        endpoint = ask("hello")["endpoint"]
    except (OSError, RuntimeError):
        print(json.dumps({"error": NO_BROWSER}))
        return 1
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(endpoint)
        page = current_tab(browser.contexts[0]) if browser.contexts else None
        if page is None:
            print(json.dumps({"error": "the browser has no tab open"}))
            return 1
        if (action := request.get("input")) is not None:
            if problem := _person_acts(page, action):
                print(json.dumps({"error": problem}))
                return 1
            SNAPSHOT.quiet_wait(page, 1_000)  # what the input started, briefly: the next look shows the rest
        viewport = page.viewport_size or page.evaluate("({width: innerWidth, height: innerHeight})")
        image = page.screenshot(type="jpeg", quality=LIVE_QUALITY, timeout=ACTION_TIMEOUT_MS)
        print(json.dumps({
            "url": page.url,
            "title": page.title(),
            "width": viewport["width"],
            "height": viewport["height"],
            "image": base64.b64encode(image).decode("ascii"),
        }))
    return 0


def _person_acts(page: Any, action: Any) -> str | None:
    """Do what the person did on the live view; why not, if it is not something it takes.
    Points are in the page's CSS pixels (the live image's size)."""
    kind = action.get("kind") if isinstance(action, dict) else None
    try:
        if kind == "click":
            page.mouse.click(float(action["x"]), float(action["y"]))
        elif kind == "type" and isinstance(action.get("text"), str) and len(action["text"]) <= 1_000:
            page.keyboard.type(action["text"])
        elif kind == "key" and isinstance(action.get("key"), str) and LIVE_KEY.match(action["key"]):
            page.keyboard.press(action["key"])
        elif kind == "scroll":
            page.mouse.move(float(action.get("x", 0)), float(action.get("y", 0)))
            page.mouse.wheel(float(action.get("dx", 0)), float(action.get("dy", 0)))
        else:
            return f"not an input the live view takes: {json.dumps(action)[:200]}"
    except (KeyError, TypeError, ValueError):
        return f"an input with what it needs (a click's x and y, a key's name): {json.dumps(action)[:200]}"
    return None


def main() -> int:
    if sys.argv[1:] == ["fill"]:
        return fill()
    if sys.argv[1:] == ["live"]:
        return live()
    if sys.argv[1:] == ["dialog"]:
        return dialog()
    if sys.argv[1:] == ["vault-inspect"]:
        return vault_inspect()
    if sys.argv[1:] == ["vault-write"]:
        return vault_write()
    if sys.argv[1:] == ["cdp"]:
        return cdp_call()
    code = sys.stdin.read()
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    Path(DAEMON.HOME).mkdir(parents=True, exist_ok=True)
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        done = threading.Event()
        try:
            hello = _hello()
            started = started_afresh(hello)
            if hello.get("supervised"):  # before connecting: a page a dialog blocks blocks that too
                threading.Thread(target=_watch_dialogs, args=(started, done), daemon=True).start()
            session = Session(playwright.chromium.connect_over_cdp(hello["endpoint"]), started)
        except Exception as exc:  # noqa: BLE001 — reported to the model as the call's error
            done.set()
            _meta(error=f"browser unavailable: {exc}", started=False)
            return 2
        failed = None
        try:
            exec(compile(code, "<browser_exec>", "exec"), Helpers(session).scope())  # noqa: S102 — code mode
        except Exception:  # noqa: BLE001 — the code's own error, shown as Python shows it
            failed = _traceback()
        finally:
            with contextlib.suppress(Exception):
                ask("dialogs", action="hold")  # between calls, a dialog waits for browser_dialog
        # The watcher keeps watching: reading the tab back can wait on a dialog too.
        session.remember()
        url = ""
        with contextlib.suppress(Exception):
            url = session.page.url
        events: list[dict[str, Any]] = []
        with contextlib.suppress(Exception):
            events = session.reported()
        shown = [str(Path(p).relative_to(WORKSPACE)) for p in session.shown[-MAX_SHOWN:]]
        with _REPORTING:
            done.set()
            _meta(started=session.started, url=url, error=failed, events=events, shown=shown, pending_dialogs=_pending())
    return 1 if failed else 0


_REPORTING = threading.Lock()
"""Held while the call's result is written: by the call as it ends, or by the dialog watcher."""


def _pending() -> list[dict[str, Any]]:
    """The dialogs held for ``browser_dialog`` now."""
    with contextlib.suppress(Exception):
        return list(ask("pending")["pending_dialogs"])
    return []


def _watch_dialogs(started: bool, done: threading.Event) -> None:
    """End the call when a held dialog blocks the page: its JavaScript waits on the dialog,
    so the code's next step on the page would wait out the call. What the code printed so
    far is kept; the result says which dialog, and how to answer it."""
    first_seen: dict[str, float] = {}
    while not done.wait(0.25):
        pending = _pending()
        now = time.monotonic()
        for dialog in pending:
            first_seen.setdefault(dialog["id"], now)
        blocking = [d for d in pending if now - first_seen[d["id"]] >= BLOCKED_AFTER_S]
        if not blocking:
            continue
        _REPORTING.acquire()
        if done.is_set():  # the call is writing its own result
            _REPORTING.release()
            return
        sys.stdout.flush()
        events: list[dict[str, Any]] = []
        with contextlib.suppress(Exception):
            events = reported()
        _meta(
            started=started,
            error=(
                f"{_article(blocking[0]['type'])} {blocking[0]['type']} dialog is open and the page waits on it: answer it with browser_dialog "
                '(accept or dismiss), or call answer_dialogs("accept") before the step that opens one'
            ),
            events=events,
            pending_dialogs=pending,
            blocked=True,
        )
        sys.stderr.flush()
        os._exit(1)


def _vault_session(playwright: Any) -> Session | None:
    """The browser for a vault step: connected, never started (nothing to fill without a page)."""
    try:
        hello = ask("hello")
        return Session(playwright.chromium.connect_over_cdp(hello["endpoint"]), started=False)
    except (OSError, RuntimeError):
        print(json.dumps({"error": NO_BROWSER}))
        return None


def vault_inspect() -> int:
    """The tab a saved item is filled in, and its form's controls: the current tab when it is
    on one of the item's origins, else an open tab on one that holds such a form (Hermes's
    ``focus_page``); its inputs are stamped with the nonce for ``vault-write``.
    ``{"tab", "origin", "url", "controls": [...]}``, or ``{"error", "error_type"}``."""
    request = json.loads(os.environ.pop(VAULT_ENV, "") or "{}")
    origins, kind, nonce = request.get("origins"), str(request.get("kind", "")), str(request.get("nonce", ""))
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        session = _vault_session(playwright)
        if session is None:
            return 1
        page = _vault_tab(session, origins, kind)
        if page is None:
            on = origin(session.page.url) if session.page.url.startswith(("http://", "https://")) else session.page.url
            print(json.dumps({
                "error": (
                    f"Refused: the current page ({on or 'no page'}) is not on the item's site "
                    f"({', '.join(origins or []) or 'none saved'}), and no open tab there holds its form: open its page first."
                ),
                "error_type": "origin_mismatch",
            }))
            return 1
        if not nonce:  # where the form is, nothing more
            print(json.dumps({"tab": _target_id(page), "origin": origin(page.url), "url": page.url}))
            return 0
        try:
            raw = page.evaluate(FORMS.build_inspection_js(nonce))
        except Exception as exc:  # noqa: BLE001 — the page went
            print(json.dumps({"error": f"could not inspect the page's inputs: {type(exc).__name__}"}))
            return 1
        session.page = page
        session.remember()
        print(json.dumps({"tab": _target_id(page), "origin": origin(page.url), "url": page.url, "controls": json.loads(raw)}))
    return 0


def _vault_tab(session: Session, origins: list[str] | None, kind: str) -> Any | None:
    """The current tab if it is on one of ``origins``; else the first open tab on one of them
    holding the form ``kind`` fills (by its probe). ``origins`` None: any site's tab holding
    such a form (where a login is to be saved)."""

    def on_site(page: Any) -> bool:
        return page.url.startswith(("http://", "https://")) if origins is None else origin(page.url) in origins

    probe = FORMS.TAB_PROBES.get(kind)
    if on_site(session.page) and (origins is not None or _probed(session.page, probe)):
        return session.page
    return next((p for p in session.context.pages if p is not session.page and on_site(p) and _probed(p, probe)), None)


def _probed(page: Any, probe: str | None) -> bool:
    """Whether the page holds the form ``probe`` looks for (no probe: any page does)."""
    with contextlib.suppress(Exception):
        return probe is None or bool(page.evaluate(probe))
    return False


def vault_write() -> int:
    """Run the fill script in the tab ``vault-inspect`` chose: ``{"filled": n}``, or
    ``{"refused": "origin_changed", "found"}`` when the page left the item's site in between.
    Nothing it answers holds a value."""
    request = json.loads(os.environ.pop(VAULT_ENV, "") or "{}")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        session = _vault_session(playwright)
        if session is None:
            return 1
        page = next((p for p in session.context.pages if _target_id(p) == request.get("tab")), None)
        if page is None:
            print(json.dumps({"error": "the tab with the form closed before the fill: nothing was written"}))
            return 1
        try:
            answer = json.loads(page.evaluate(request["expression"]))
        except Exception:  # noqa: BLE001 — its message could quote the script, which holds the values
            print(json.dumps({"error": "the page did not run the fill: nothing was written"}))
            return 1
        print(json.dumps({k: v for k, v in answer.items() if k in ("filled", "refused", "found")}))
    return 0


def _article(word: str) -> str:
    return "an" if word[:1].lower() in "aeiou" else "a"


def dialog() -> int:
    """``browser_dialog``: answer the dialog the supervisor holds; ``{"ok", "dialog"}`` or ``{"error"}``."""
    request = json.loads(os.environ.pop(DIALOG_ENV, "") or "{}")
    try:
        answer = told("respond", wait=20.0, **request)
    except OSError:
        print(json.dumps({"ok": False, "error": NO_BROWSER}))
        return 1
    print(json.dumps(answer))
    return 0 if answer.get("ok") else 1


def cdp_call() -> int:
    """``browser_cdp``: one raw CDP command over the supervisor's connection."""
    request = json.loads(os.environ.pop(CDP_ENV, "") or "{}")
    try:
        answer = told("cdp", wait=float(request.get("timeout", 30.0)) + 5.0, **request)
    except OSError:
        print(json.dumps({"ok": False, "error": NO_BROWSER}))
        return 1
    print(json.dumps(answer, default=str))
    return 0 if answer.get("ok") else 1


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
