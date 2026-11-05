"""The browser code mode's runner: executes one ``browser_exec`` call inside the sandbox.

Shipped into the sandbox as source and run with the sandbox's own Python, so it imports
nothing from the supervisor: the standard library and Playwright only. It reads the
model's code on stdin and runs it with a Playwright ``page`` (and ``context``,
``browser``) already connected to the sandbox's browser, beside helpers in the shape
Hermes's browser-harness offers (``new_tab``, ``goto_url``, ``js``, ``cdp``, ...).

The browser outlives the call: the runner starts a headless Chromium once per browser
name, in a session of its own and without the call's run tag, so neither the call ending
nor a stop of the call kills it, and every later call reconnects to it over CDP. A
browser endpoint given in ``GITHI_BROWSER_CDP`` (a provider's own browser) is used
instead of starting one.

What the code prints is the result. On stderr, after anything the code wrote there, one
line starting with ``META_MARK`` carries what the tool reports besides (the page the call
ended on, whether a fresh browser had to be started, the call's error).
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import time
import traceback
import urllib.request
from pathlib import Path
from typing import Any

META_MARK = "\x1egithi-browser "
"""Starts the stderr line the tool reads back (an ASCII record separator, then a tag)."""
ROOT = Path(os.environ.get("GITHI_BROWSER_HOME") or "/tmp/githi-browser")
"""Per-sandbox state: one directory per browser name (profile, port, current tab)."""
WORKSPACE = Path(os.environ.get("BROWSER_WORKSPACE") or ".githi/browser").resolve()
"""In the session's workspace, so it is saved with the session: batch files, helpers."""
NAME = os.environ.get("GITHI_BROWSER_NAME") or "default"
RUN_TAG_VAR = "SUPERVISOR_RUN_TAG"
"""The variable a stop finds a call's processes by; the browser must not carry it."""
START_TIMEOUT_S = 20.0
ACTION_TIMEOUT_MS = 5_000
"""How long an action waits for its element: a locator that names nothing fails in
seconds, not Playwright's 30 (the code can pass ``timeout=`` for a slow element)."""
NAVIGATION_TIMEOUT_MS = 30_000
INTERNAL = ("chrome://", "chrome-untrusted://", "devtools://", "chrome-extension://")


class Session:
    """This call's view of the browser: the page code acts on, how it got there, and the
    dialogs its pages opened."""

    def __init__(self, browser: Any, started: bool) -> None:
        self.browser = browser
        self.context = browser.contexts[0] if browser.contexts else browser.new_context()
        self.context.set_default_timeout(ACTION_TIMEOUT_MS)
        self.context.set_default_navigation_timeout(NAVIGATION_TIMEOUT_MS)
        self.started = started
        self.dialog_answer: tuple[str, str | None] = ("dismiss", None)
        self.dialogs: list[dict[str, str]] = []
        for page in self.context.pages:
            page.on("dialog", self._answer)
        self.context.on("page", lambda page: page.on("dialog", self._answer))
        self.page = self._current()

    def _answer(self, dialog: Any) -> None:
        """Answer a native dialog (alert, confirm, prompt, beforeunload) as the code asked,
        dismissing by default, and keep it for the result: none passes unseen."""
        action, text = self.dialog_answer
        with contextlib.suppress(Exception):  # closed with its page
            if action == "accept":
                dialog.accept(text)
            else:
                dialog.dismiss()
        self.dialogs.append({"type": dialog.type, "message": dialog.message, "answer": action})

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


def _state_file(name: str) -> Path:
    return ROOT / NAME / name


def _target_id(page: Any) -> str:
    cdp = page.context.new_cdp_session(page)
    try:
        return str(cdp.send("Target.getTargetInfo")["targetInfo"]["targetId"])
    finally:
        cdp.detach()


def _alive(endpoint: str) -> bool:
    try:
        with urllib.request.urlopen(f"{endpoint}/json/version", timeout=2) as reply:
            return reply.status == 200
    except OSError:
        return False


def _endpoint(chromium_path: str) -> tuple[str, bool]:
    """The browser's CDP endpoint, and whether this call had to start it."""
    if given := os.environ.get("GITHI_BROWSER_CDP"):
        return given, False
    port_file = _state_file("port")
    if port_file.exists() and _alive(endpoint := f"http://127.0.0.1:{port_file.read_text().strip()}"):
        return endpoint, False
    return _start(chromium_path), True


def _start(chromium_path: str) -> str:
    """Start a headless Chromium of its own (detached, untagged) and wait for its port."""
    profile = ROOT / NAME / "profile"
    profile.mkdir(parents=True, exist_ok=True)
    active = profile / "DevToolsActivePort"
    active.unlink(missing_ok=True)
    env = {k: v for k, v in os.environ.items() if k != RUN_TAG_VAR}
    with open(ROOT / NAME / "chromium.log", "ab") as log:
        subprocess.Popen(
            [
                chromium_path,
                "--headless=new",
                "--remote-debugging-port=0",
                f"--user-data-dir={profile}",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-dev-shm-usage",
                # The sandbox it runs in is the isolation boundary (as Playwright's own
                # launch assumes): Chromium's sandbox needs user namespaces, which Docker
                # with no capabilities, and Ubuntu 24.04+ for unprivileged users, deny.
                "--no-sandbox",
                "about:blank",
            ],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            env=env,
            start_new_session=True,  # its own session: the call's end or stop leaves it
        )
    deadline = time.monotonic() + START_TIMEOUT_S
    while time.monotonic() < deadline:
        if active.exists() and (port := active.read_text().split("\n", 1)[0].strip()):
            endpoint = f"http://127.0.0.1:{port}"
            if _alive(endpoint):
                _state_file("port").write_text(port)
                return endpoint
        time.sleep(0.1)
    raise RuntimeError(f"Chromium did not start within {START_TIMEOUT_S:.0f}s (see {ROOT / NAME / 'chromium.log'})")


def helpers(session: Session) -> dict[str, Any]:
    """The names code runs with: Playwright's objects and Hermes-shaped helpers on them."""

    def new_tab(url: str = "about:blank") -> Any:
        """Open ``url`` in a new tab and make it ``page``."""
        session.page = session.context.new_page()
        if url != "about:blank":
            session.page.goto(url, wait_until="domcontentloaded")
        scope["page"] = session.page
        return session.page

    def goto_url(url: str) -> Any:
        return session.page.goto(url, wait_until="domcontentloaded")

    def wait_for_load(timeout: float = 15.0) -> bool:
        try:
            session.page.wait_for_load_state("load", timeout=timeout * 1000)
        except Exception:  # noqa: BLE001 — a slow page is an answer, not a failure
            return False
        return True

    def page_info() -> dict[str, Any]:
        return dict(
            session.page.evaluate(
                "() => ({url: location.href, title: document.title, w: innerWidth, h: innerHeight,"
                " sx: scrollX, sy: scrollY, pw: document.documentElement.scrollWidth,"
                " ph: document.documentElement.scrollHeight})"
            )
        )

    def js(expression: str) -> Any:
        """Evaluate a JavaScript expression (or a function) in the page; its value."""
        return session.page.evaluate(expression)

    def cdp(method: str, **params: Any) -> Any:
        """Send one raw CDP command for the current page."""
        client = session.context.new_cdp_session(session.page)
        try:
            return client.send(method, params)
        finally:
            client.detach()

    def fill_input(selector: str, text: str) -> None:
        session.page.fill(selector, text)

    def click_at_xy(x: float, y: float) -> None:
        session.page.mouse.click(x, y)

    def capture_screenshot(path: str | None = None, full: bool = False) -> str:
        """Save a screenshot (in the workspace by default) and print its path."""
        if path is None:
            shots = WORKSPACE / "screenshots"
            shots.mkdir(parents=True, exist_ok=True)
            path = str(shots / f"shot-{time.strftime('%Y%m%d-%H%M%S')}-{time.monotonic_ns() % 10**6}.png")
        session.page.screenshot(path=path, full_page=full)
        print(path)
        return path

    def list_tabs() -> list[dict[str, str]]:
        return [{"id": _target_id(p), "url": p.url, "title": p.title()} for p in session.context.pages]

    def switch_tab(tab_id: str) -> Any:
        for candidate in session.context.pages:
            if _target_id(candidate) == tab_id:
                session.page = scope["page"] = candidate
                candidate.bring_to_front()
                return candidate
        raise ValueError(f"no tab {tab_id!r}; list_tabs() shows the open ones")

    def answer_dialogs(action: str = "accept", text: str | None = None) -> None:
        """How this call answers native dialogs: "accept" (with ``text`` for a prompt) or
        "dismiss" (the default). Every dialog is listed in the result either way."""
        if action not in ("accept", "dismiss"):
            raise ValueError('answer_dialogs takes "accept" or "dismiss"')
        session.dialog_answer = (action, text)

    def ensure_real_tab() -> Any:
        """Make ``page`` a real, open tab again (after it closed, or landed on an internal one)."""
        session.page = scope["page"] = session._current()
        return session.page

    scope: dict[str, Any] = {
        "__name__": "__main__",
        "page": session.page,
        "context": session.context,
        "browser": session.browser,
        "WORKSPACE": str(WORKSPACE),
    }
    scope.update(
        {
            fn.__name__: fn
            for fn in (
                new_tab, goto_url, wait_for_load, page_info, js, cdp, fill_input,
                click_at_xy, capture_screenshot, list_tabs, switch_tab, answer_dialogs, ensure_real_tab,
            )
        }
    )
    _load_agent_helpers(scope)
    return scope


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
    (ROOT / NAME).mkdir(parents=True, exist_ok=True)
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        try:
            endpoint, started = _endpoint(playwright.chromium.executable_path)
            session = Session(playwright.chromium.connect_over_cdp(endpoint), started)
        except Exception as exc:  # noqa: BLE001 — reported to the model as the call's error
            _meta(error=f"browser unavailable: {exc}", started=False)
            return 2
        failed = None
        try:
            exec(compile(code, "<browser_exec>", "exec"), helpers(session))  # noqa: S102 — code mode
        except Exception:  # noqa: BLE001 — the code's own error, shown as Python shows it
            failed = _traceback()
        session.remember()
        url = ""
        with contextlib.suppress(Exception):
            url = session.page.url
        _meta(started=started, url=url, error=failed, dialogs=session.dialogs)
    return 1 if failed else 0


def _traceback() -> str:
    """The code's traceback, without the runner's frames or Playwright's internals."""
    kind, value, tb = sys.exc_info()
    frames = traceback.extract_tb(tb)
    own = [f for f in frames if f.filename != __file__ and f"{os.sep}playwright{os.sep}" not in f.filename]
    lines = traceback.format_list(own or frames[-1:]) + traceback.format_exception_only(kind, value)
    return "Traceback (most recent call last):\n" + "".join(lines)


if __name__ == "__main__":
    sys.exit(main())
