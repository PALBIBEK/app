"""``browser_exec`` against a real headless Chromium, run as a sandbox runs it.

The workspace runs each call's command as a sandbox does (a shell in the workspace root
with a scrubbed environment, killed with everything it started on a stop). Tests that use
``call`` need the sandbox's runtime on this machine (python3 with Playwright and its
Chromium); ``needs_browser`` skips them elsewhere. CI installs it.
"""

from __future__ import annotations

import dataclasses
import hashlib
import os
import shutil
import signal
import subprocess
import threading
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from supervisor.catalog import DigestRef, FileEffects, SandboxProviderName
from supervisor.catalog.repository import persist_offer
from supervisor.temporal.contracts import CreateSession
from supervisor.tools import default_registry
from supervisor.tools.context import ToolContext
from supervisor.tools.workspace import HostWorkspace

if TYPE_CHECKING:
    from supervisor.app import SupervisorState
    from tests.support.githi_env import Env

__all__ = [
    "BROWSERS_ENV",
    "Call",
    "Site",
    "Tool",
    "browser_session",
    "call",
    "default_browsers",
    "kill",
    "needs_browser",
    "site",
    "stop_browsers",
    "tool",
]

needs_browser = pytest.mark.skipif(
    subprocess.run(["python3", "-c", "import playwright"], capture_output=True, check=False).returncode != 0,
    reason="no browser runtime here (python3 with Playwright and Chromium)",
)
BROWSERS_ENV = {
    "PLAYWRIGHT_BROWSERS_PATH": os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or str(Path.home() / ".cache" / "ms-playwright"),
    **{k: v for k, v in os.environ.items() if k == "PLAYWRIGHT_HOST_PLATFORM_OVERRIDE"},
}
"""Where this machine keeps Playwright's browsers, and which build it uses: a workspace
command's HOME is the workspace, so Playwright's default would look there (a sandbox
image sets its own)."""

Call = Callable[..., Awaitable[dict[str, Any]]]
Tool = Callable[..., Awaitable[dict[str, Any]]]


def _context(tmp_path: Path, stop: threading.Event | None = None) -> ToolContext:
    """The context ``call`` and ``tool`` run in: one workspace, one browser home."""
    (tmp_path / "ws").mkdir(exist_ok=True)
    return ToolContext(
        bound_workspace=HostWorkspace(tmp_path / "ws"),
        timeout_s=60,
        env={"GITHI_BROWSER_HOME": str(tmp_path / "browser-home"), **BROWSERS_ENV},
        stop=stop,
    )


@pytest.fixture
async def call(tmp_path: Path) -> AsyncIterator[Call]:
    """``browser_exec`` on a workspace of its own, with a browser home of its own."""
    (tmp_path / "ws").mkdir(exist_ok=True)
    spec = default_registry()["browser_exec"]

    async def run(code: str, stop: threading.Event | None = None, **args: Any) -> dict[str, Any]:
        return dict(await spec.run({"code": code, **args}, _context(tmp_path, stop)))

    yield run
    stop_browsers(tmp_path / "browser-home")


@pytest.fixture
def tool(tmp_path: Path) -> Tool:
    """Another browser tool (``browser_dialog``, ``browser_cdp``), by name, on ``call``'s
    workspace and browser."""

    async def run(name: str, **args: Any) -> dict[str, Any]:
        return dict(await default_registry()[name].run(args, _context(tmp_path)))

    return run


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


# A githi session with a browser, from the seams -------------------------------------


async def browser_session(env: Env, state: SupervisorState, **profile_changes: Any) -> str:
    """A session on the shipped composition, with the browser tool set and a host-process
    sandbox profile changed by ``profile_changes``. A host process isolates nothing, so a
    tool that acts outside the workspace runs there only on ``danger_full_access``."""
    catalog = state.composition_catalog
    assert catalog is not None
    latest = lambda kind, name: catalog.get(kind, name, catalog.list_versions(kind, name)[-1])
    base, tools = latest("composition", "githi-v1"), latest("tool_set", "githi-browser-tools")
    profile = catalog.offer(
        dataclasses.replace(
            latest("sandbox_profile", "githi-docker-v1"),
            name=f"browser-{uuid.uuid4().hex[:6]}",
            digest="",
            provider=SandboxProviderName.LOCAL_PROCESS,
            image_digest=f"sha256:{hashlib.sha256(b'host').hexdigest()}",
            file_effects=FileEffects.DANGER_FULL_ACCESS,
            **profile_changes,
        )
    )
    await persist_offer(env.factory, catalog, "sandbox_profile", profile.name, profile.digest)  # type: ignore[arg-type]
    composition = catalog.offer(
        dataclasses.replace(
            base,
            name=f"browser-agent-{uuid.uuid4().hex[:6]}",
            digest="",
            tool_set=DigestRef(id=tools.name, digest=tools.digest),
            sandbox_profile=DigestRef(id=profile.name, digest=profile.digest),
        )
    )
    await persist_offer(env.factory, catalog, "composition", composition.name, composition.digest)  # type: ignore[arg-type]
    sid = uuid.uuid4().hex
    await env.sessions.create(
        sid, CreateSession("t1", "githi-v1", composition_id=composition.name, composition_digest=composition.digest, principal_id="alice")
    )
    return sid


@pytest.fixture
def default_browsers() -> Iterator[Path]:
    """A host-process sandbox runs commands with a scrubbed environment and the real HOME,
    so its runner finds Playwright's browsers where Playwright looks by default: point that
    at this machine's for the test (a sandbox image has its own). The browser home the
    runner then uses is its default; the test stops only the browsers it started there."""
    default = Path.home() / ".cache" / "ms-playwright"
    made = not default.exists()
    if made:
        default.parent.mkdir(parents=True, exist_ok=True)
        default.symlink_to(BROWSERS_ENV["PLAYWRIGHT_BROWSERS_PATH"])
    home = Path("/tmp/githi-browser")
    before = {p.read_text() for p in home.glob("*/daemon.pid")}
    yield home
    for pid_file in home.glob("*/daemon.pid"):
        if pid_file.read_text() not in before:
            kill(int(pid_file.read_text()))
    if made:
        default.unlink()
