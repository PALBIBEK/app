"""The browser code mode on a remote browser (``GITHI_BROWSER_CDP``), as a hosted
provider's session is reached: the sandbox's daemon connects to it instead of starting
one, keeps it across calls, notices when the session's browser is another one, and
reports a provider that could not serve the call. A Chromium started here stands in for
the hosted one."""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

from supervisor.tools import default_registry
from supervisor.tools.context import ToolContext
from supervisor.tools.workspace import HostWorkspace
from tests.support.browser import BROWSERS_ENV, needs_browser, stop_browsers

pytestmark = [pytest.mark.asyncio, needs_browser]


def _remote_browser(profile: Path) -> tuple[subprocess.Popen[bytes], str]:
    """A Chromium of its own, and its CDP WebSocket URL (what a provider's session gives)."""
    chromium = subprocess.run(
        ["python3", "-c", "from playwright.sync_api import sync_playwright as s; p = s().start(); print(p.chromium.executable_path); p.stop()"],
        capture_output=True, text=True, check=True, env={**os.environ, **BROWSERS_ENV},
    ).stdout.strip()
    proc = subprocess.Popen(
        [chromium, "--headless=new", "--no-sandbox", "--remote-debugging-port=0", f"--user-data-dir={profile}", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    active = profile / "DevToolsActivePort"
    for _ in range(100):
        if active.exists() and active.read_text().strip():
            port = active.read_text().split()[0]
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2) as reply:
                return proc, str(json.loads(reply.read())["webSocketDebuggerUrl"])
        time.sleep(0.1)
    raise RuntimeError("the stand-in remote browser did not start")


@pytest.fixture
def remotes(tmp_path: Path) -> Iterator[list[str]]:
    """Two stand-in remote browsers' URLs."""
    started = [_remote_browser(tmp_path / f"remote-{i}") for i in range(2)]
    yield [url for _, url in started]
    for proc, _ in started:
        proc.kill()


async def _call(tmp_path: Path, code: str, **env: str) -> dict:
    ctx = ToolContext(
        bound_workspace=HostWorkspace(tmp_path),
        timeout_s=60,
        env={"GITHI_BROWSER_HOME": str(tmp_path / "home"), **BROWSERS_ENV, **env},
    )
    return dict(await default_registry()["browser_exec"].run({"code": code}, ctx))


async def test_the_daemon_connects_to_the_remote_browser_and_keeps_it(tmp_path: Path, remotes: list[str]) -> None:
    try:
        first = await _call(tmp_path, "page.set_content('<title>remote</title>')", GITHI_BROWSER_CDP=remotes[0])
        again = await _call(tmp_path, "print(page.title())", GITHI_BROWSER_CDP=remotes[0])
    finally:
        stop_browsers(tmp_path / "home")

    assert first["exit_code"] == 0 and first["browser_started"] is True
    assert again["output"] == "remote" and "browser_started" not in again
    assert not list((tmp_path / "home").glob("*/profile")), "a browser was started in the sandbox too"


async def test_another_remote_browser_is_a_fresh_one(tmp_path: Path, remotes: list[str]) -> None:
    """The provider's session ended and the next call got another: the daemon follows it,
    and the result says the earlier tabs are gone."""
    try:
        await _call(tmp_path, "page.set_content('<title>first</title>')", GITHI_BROWSER_CDP=remotes[0])
        moved = await _call(tmp_path, "print(repr(page.title()))", GITHI_BROWSER_CDP=remotes[1])
    finally:
        stop_browsers(tmp_path / "home")

    assert moved["browser_started"] is True and moved["output"] == "''", moved


async def test_a_provider_that_could_not_serve_the_call_is_reported(tmp_path: Path) -> None:
    refused = await _call(tmp_path, "print(1)", GITHI_BROWSER_ERROR="the remote browser provider did not open a browser: 402")

    assert refused["exit_code"] == 2
    assert "did not open a browser: 402" in refused["error"]
    assert "hint" not in refused, "a provider's refusal is not a missing runtime"
