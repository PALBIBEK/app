"""``browser_exec`` against a real headless Chromium, run as a sandbox runs it.

The workspace runs each call's command as a sandbox does (a shell in the workspace root
with a scrubbed environment, killed with everything it started on a stop). Tests that use
``call`` need the sandbox's runtime on this machine (python3 with Playwright and its
Chromium); ``needs_browser`` skips them elsewhere. CI installs it.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import threading
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from supervisor.tools import default_registry
from supervisor.tools.context import ToolContext
from supervisor.tools.workspace import HostWorkspace

__all__ = ["Call", "Site", "call", "needs_browser", "site"]

needs_browser = pytest.mark.skipif(
    subprocess.run(["python3", "-c", "import playwright"], capture_output=True, check=False).returncode != 0,
    reason="no browser runtime here (python3 with Playwright and Chromium)",
)
_BROWSERS = {
    "PLAYWRIGHT_BROWSERS_PATH": os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or str(Path.home() / ".cache" / "ms-playwright"),
    **{k: v for k, v in os.environ.items() if k == "PLAYWRIGHT_HOST_PLATFORM_OVERRIDE"},
}
"""Where this machine keeps Playwright's browsers, and which build it uses: a workspace
command's HOME is the workspace, so Playwright's default would look there (a sandbox
image sets its own)."""

Call = Callable[..., Awaitable[dict[str, Any]]]


@pytest.fixture
async def call(tmp_path: Path) -> AsyncIterator[Call]:
    """``browser_exec`` on a workspace of its own, with a browser home of its own."""
    home = tmp_path / "browser-home"
    (tmp_path / "ws").mkdir()
    spec = default_registry()["browser_exec"]

    async def run(code: str, stop: threading.Event | None = None, **args: Any) -> dict[str, Any]:
        ctx = ToolContext(
            bound_workspace=HostWorkspace(tmp_path / "ws"),
            timeout_s=60,
            env={"GITHI_BROWSER_HOME": str(home), **_BROWSERS},
            stop=stop,
        )
        return dict(await spec.run({"code": code, **args}, ctx))

    yield run
    stop_browsers(home)


def stop_browsers(home: Path) -> None:
    """Daemons and browsers outlive their calls by design: stop the ones under ``home``."""
    for pid_file in home.glob("*/daemon.pid"):
        kill(int(pid_file.read_text()))
    subprocess.run(["pkill", "-f", "--", f"--user-data-dir={home}"], check=False)
    shutil.rmtree(home, ignore_errors=True)


def kill(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


class Site:
    """A local web server for pages a test needs from a real origin: ``pages`` maps a path
    to (HTML, seconds to wait before answering). Reachable as 127.0.0.1 and as localhost,
    which are different sites to the browser."""

    def __init__(self) -> None:
        self.pages: dict[str, tuple[str, float]] = {}
        site = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                body, delay = site.pages.get(self.path, ("ok", 0.0))
                time.sleep(delay)
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body.encode())

            def log_message(self, *_: object) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]

    def url(self, path: str, host: str = "127.0.0.1") -> str:
        return f"http://{host}:{self.port}{path}"


@pytest.fixture
def site() -> Iterator[Site]:
    served = Site()
    threading.Thread(target=served.server.serve_forever, daemon=True).start()
    yield served
    served.server.shutdown()
