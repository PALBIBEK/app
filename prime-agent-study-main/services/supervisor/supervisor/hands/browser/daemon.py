"""The browser daemon: one per browser name in the sandbox, holding what outlives a call.

Each ``browser_exec`` call is a fresh process (``runner.py``), so a stop can kill it and
everything it started. What must last between calls lives here instead, in a process of
its own session without the call's run tag (Hermes's browser-harness daemon has the same
role):

* **the browser**: Chromium is started here, once, and its endpoint handed to each call
  (or a provider's browser, ``GITHI_BROWSER_CDP``, is reached instead);
* **one CDP connection with an event buffer**: dialogs, console errors, page errors, main
  frame navigations and request counts of every tab, numbered, the last ``BUFFER`` kept,
  so a call sees what happened since the previous one and while nothing was connected;
* **dialogs**: answered as the current call asked, else dismissed so a page never hangs on
  one, and every one recorded;
* **network activity per tab**: requests in flight and the time since the last one, for a
  network-idle wait that counts requests begun before the call.

The model's code never runs here: a stop must be able to end it.

Like the runner, it imports nothing from the supervisor: the standard library and
Playwright's async API. Requests come one per connection on a Unix socket, as a JSON line,
and are answered with one.
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(os.environ.get("GITHI_BROWSER_HOME") or "/tmp/githi-browser")
NAME = os.environ.get("GITHI_BROWSER_NAME") or "default"
HOME = ROOT / NAME
SOCKET = Path(tempfile.gettempdir()) / f"githi-browser-{hashlib.sha256(str(HOME).encode()).hexdigest()[:12]}.sock"
"""Under the temp dir, by a hash: a Unix socket's path is capped at 108 bytes."""
RUN_TAG_VAR = "SUPERVISOR_RUN_TAG"
BUFFER = 500
START_TIMEOUT_S = 20.0
TEXT_CAP = 300
"""Characters of a console message or error kept per event."""


def _alive(endpoint: str) -> bool:
    try:
        with urllib.request.urlopen(f"{endpoint}/json/version", timeout=2) as reply:
            return bool(reply.status == 200)
    except OSError:
        return False


def _untagged_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k != RUN_TAG_VAR}


def start_chromium(chromium_path: str) -> str:
    """Start a headless Chromium of its own (detached, untagged) and wait for its port."""
    profile = HOME / "profile"
    profile.mkdir(parents=True, exist_ok=True)
    active = profile / "DevToolsActivePort"
    active.unlink(missing_ok=True)
    with open(HOME / "chromium.log", "ab") as log:
        subprocess.Popen(
            [
                chromium_path,
                "--headless=new",
                "--remote-debugging-port=0",
                f"--user-data-dir={profile}",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-dev-shm-usage",
                # No traffic of its own (updates, sync, sign-in, field trials): only what pages
                # ask for, which an egress allow-list then decides. Playwright's own switches.
                "--disable-background-networking",
                "--disable-component-update",
                "--disable-sync",
                "--disable-default-apps",
                "--disable-domain-reliability",
                "--disable-client-side-phishing-detection",
                "--disable-features=OptimizationHints,Translate,MediaRouter,AutofillServerCommunication",
                "--no-service-autorun",
                "--no-pings",
                "--password-store=basic",
                "--use-mock-keychain",
                # The sandbox it runs in is the isolation boundary (as Playwright's own
                # launch assumes): Chromium's sandbox needs user namespaces, which Docker
                # with no capabilities, and Ubuntu 24.04+ for unprivileged users, deny.
                "--no-sandbox",
                "about:blank",
            ],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            env=_untagged_env(),
            start_new_session=True,  # a session of its own: no call's end or stop reaches it
        )
    deadline = time.monotonic() + START_TIMEOUT_S
    while time.monotonic() < deadline:
        if active.exists() and (port := active.read_text().split("\n", 1)[0].strip()):
            endpoint = f"http://127.0.0.1:{port}"
            if _alive(endpoint):
                (HOME / "port").write_text(port)
                return endpoint
        time.sleep(0.1)
    raise RuntimeError(f"Chromium did not start within {START_TIMEOUT_S:.0f}s (see {HOME / 'chromium.log'})")


class Daemon:
    """The browser's one long-lived connection, its event buffer, and its dialog answers."""

    def __init__(self) -> None:
        self.events: collections.deque[dict[str, Any]] = collections.deque(maxlen=BUFFER)
        self.seq = 0
        self.generation = 0
        """Which browser this is, of all started for this name (kept in ``HOME``): a call
        that last saw another one knows its tabs and logins are gone."""
        self.endpoint = ""
        self.dialog_answer: tuple[str, str | None] = ("dismiss", None)
        self.inflight: dict[str, set[Any]] = collections.defaultdict(set)
        self.last_request: dict[str, float] = {}
        self.targets: dict[Any, str] = {}
        self.browser: Any = None
        self.done = asyncio.Event()
        """Set when the browser goes, or a call asks the daemon to stop."""

    # The browser -------------------------------------------------------------

    async def connect(self, playwright: Any) -> None:
        """Reach the browser (starting it when none answers) and watch every tab."""
        given = os.environ.get("GITHI_BROWSER_CDP")
        port = HOME / "port"
        if given:
            self.endpoint = given
            last = HOME / "remote-endpoint"
            if not last.exists() or last.read_text() != given:  # another browser than the last one
                self._count_generation()
                last.write_text(given)
        elif port.exists() and _alive(endpoint := f"http://127.0.0.1:{port.read_text().strip()}"):
            self.endpoint = endpoint
        else:
            self.endpoint = await asyncio.to_thread(start_chromium, playwright.chromium.executable_path)
            self._count_generation()
        self.generation = int(counter.read_text()) if (counter := HOME / "generation").exists() else 0
        self.browser = await playwright.chromium.connect_over_cdp(self.endpoint)
        context = self.browser.contexts[0] if self.browser.contexts else await self.browser.new_context()
        for page in context.pages:
            await self.watch(page)
        context.on("page", lambda page: asyncio.ensure_future(self.watch(page)))

    @staticmethod
    def _count_generation() -> None:
        """One more browser started for this name (counted across daemons)."""
        counter = HOME / "generation"
        counter.write_text(str(int(counter.read_text()) + 1 if counter.exists() else 1))

    async def watch(self, page: Any) -> None:
        """Record what ``page`` does, under its CDP target id (what calls name tabs by)."""
        cdp = await page.context.new_cdp_session(page)
        try:
            target = str((await cdp.send("Target.getTargetInfo"))["targetInfo"]["targetId"])
        finally:
            await cdp.detach()
        self.targets[page] = target
        page.on("dialog", lambda dialog: asyncio.ensure_future(self.on_dialog(target, dialog)))
        page.on("console", lambda msg: self.on_console(target, msg))
        page.on("pageerror", lambda error: self.record("pageerror", target, text=str(error)[:TEXT_CAP]))
        page.on("framenavigated", lambda frame: self.on_navigated(target, page, frame))
        page.on("request", lambda request: self.on_request(target, request, started=True))
        page.on("requestfinished", lambda request: self.on_request(target, request, started=False))
        page.on("requestfailed", lambda request: self.on_request(target, request, started=False))
        page.on("close", lambda _: self.record("closed", target))

    # Events --------------------------------------------------------------------

    def record(self, kind: str, target: str, **fields: Any) -> None:
        self.seq += 1
        self.events.append({"seq": self.seq, "kind": kind, "tab": target, "at": time.time(), **fields})

    async def on_dialog(self, target: str, dialog: Any) -> None:
        """Answer as the current call asked, else dismiss: a page must never hang on one."""
        action, text = self.dialog_answer
        with contextlib.suppress(Exception):  # closed with its page
            if action == "accept":
                await dialog.accept(text)
            else:
                await dialog.dismiss()
        self.record("dialog", target, type=dialog.type, message=dialog.message[:TEXT_CAP], answer=action)

    def on_console(self, target: str, message: Any) -> None:
        if message.type in ("error", "warning"):
            self.record("console", target, level=message.type, text=message.text[:TEXT_CAP])

    def on_navigated(self, target: str, page: Any, frame: Any) -> None:
        if frame is page.main_frame:
            self.record("navigated", target, url=frame.url)

    def on_request(self, target: str, request: Any, *, started: bool) -> None:
        (self.inflight[target].add if started else self.inflight[target].discard)(request)
        self.last_request[target] = time.monotonic()

    # Requests ------------------------------------------------------------------

    def answer(self, request: dict[str, Any]) -> dict[str, Any]:
        """One request from a call, by its ``op``."""
        match request.get("op"):
            case "hello":
                return {"pid": os.getpid(), "endpoint": self.endpoint, "generation": self.generation, "seq": self.seq}
            case "events":
                since = int(request.get("since", 0))
                return {"events": [e for e in self.events if e["seq"] > since], "seq": self.seq}
            case "dialogs":
                action = request.get("action", "dismiss")
                if action not in ("accept", "dismiss"):
                    return {"error": 'dialogs are answered "accept" or "dismiss"'}
                self.dialog_answer = (action, request.get("text"))
                return {"ok": True}
            case "shutdown":
                self.done.set()
                return {"ok": True}
            case "network":
                tab = str(request.get("tab", ""))
                last = self.last_request.get(tab)
                return {"inflight": len(self.inflight.get(tab, ())), "quiet_ms": None if last is None else int((time.monotonic() - last) * 1000)}
            case op:
                return {"error": f"unknown op {op!r}"}

    async def serve(self) -> None:
        SOCKET.unlink(missing_ok=True)
        server = await asyncio.start_unix_server(self._connection, path=str(SOCKET))
        os.chmod(SOCKET, 0o600)
        (HOME / "daemon.pid").write_text(str(os.getpid()))
        self.browser.on("disconnected", lambda _: self.done.set())
        async with server:
            await self.done.wait()  # the browser is gone (or a call asked): so is the reason to run
        SOCKET.unlink(missing_ok=True)

    async def _connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = await reader.readline()
            try:
                reply = self.answer(json.loads(line))
            except ValueError:
                reply = {"error": "a request is one JSON object on one line"}
            writer.write(json.dumps(reply, default=str).encode() + b"\n")
            await writer.drain()
        finally:
            writer.close()


async def main() -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        daemon = Daemon()
        await daemon.connect(playwright)
        await daemon.serve()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:  # noqa: BLE001 — the runner reads this log when we do not answer
        with open(HOME / "daemon.log", "a") as log:
            log.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {type(exc).__name__}: {exc}\n")
        sys.exit(1)
