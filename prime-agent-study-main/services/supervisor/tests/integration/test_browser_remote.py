"""The browser code mode on a remote browser (``GITHI_BROWSER_CDP``), as a hosted
provider's session is reached: the sandbox's daemon connects to it instead of starting
one, keeps it across calls, notices when the session's browser is another one, and
reports a provider that could not serve the call. A Chromium started here stands in for
the hosted one."""

from __future__ import annotations

import asyncio
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
from tests.support.browser import BROWSERS_ENV, Site, needs_browser, stop_browsers

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


# Hermes's URL policy, hybrid routing, fallback and inactivity -------------------------------


def _remote_urls(ws_url: str) -> list[str]:
    """What the stand-in remote browser's tabs show."""
    port = ws_url.split(":", 2)[2].split("/", 1)[0]
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=2) as reply:
        return [t["url"] for t in json.loads(reply.read())]


async def test_on_a_hosted_browser_a_private_address_is_opened_by_the_sandboxs_own(tmp_path: Path, remotes: list[str], site: Site) -> None:
    """Hermes's hybrid routing: a provider cannot reach this network, and is not pointed into it."""
    site.pages["/dash"] = ("<title>dashboard</title>", 0.0)
    try:
        local = await _call(tmp_path, f"page.goto({site.url('/dash')!r})\nprint(page.title())", GITHI_BROWSER_CDP=remotes[0])
    finally:
        stop_browsers(tmp_path / "home")

    assert local["output"] == "dashboard" and "default-local" in local["local_browser"], local
    assert not any("/dash" in url for url in _remote_urls(remotes[0])), "the hosted browser was pointed at a private address"


async def test_cloud_metadata_is_refused_before_anything_runs(tmp_path: Path) -> None:
    refused = await _call(tmp_path, "page.goto('http://169.254.169.254/latest/meta-data/')")

    assert refused["blocked_by_policy"] is True and "cloud metadata" in refused["error"]
    assert not (tmp_path / "home").exists(), "a browser was started for a refused call"


async def test_when_the_hosted_browser_fails_the_sandboxs_own_serves_and_the_result_says_why(tmp_path: Path) -> None:
    try:
        served = await _call(tmp_path, "page.set_content('<title>here</title>')\nprint(page.title())", GITHI_BROWSER_FALLBACK="browserbase: did not open a browser: 401")
    finally:
        stop_browsers(tmp_path / "home")

    assert served["output"] == "here" and served["fallback_from_cloud"] == "browserbase: did not open a browser: 401"


async def test_a_hosted_browser_is_let_go_after_its_idle_time(tmp_path: Path, remotes: list[str]) -> None:
    """A provider bills by the minute: with no call for GITHI_BROWSER_IDLE_S, the daemon goes."""
    try:
        await _call(tmp_path, "print(1)", GITHI_BROWSER_CDP=remotes[0], GITHI_BROWSER_IDLE_S="1")
        pid = int((tmp_path / "home" / "default" / "daemon.pid").read_text())
        deadline = time.monotonic() + 40
        while Path(f"/proc/{pid}").exists() and time.monotonic() < deadline:
            await asyncio.sleep(0.25)
        gone = not Path(f"/proc/{pid}").exists() or "zombie" in Path(f"/proc/{pid}/status").read_text().lower()
    finally:
        stop_browsers(tmp_path / "home")

    assert gone, "the daemon kept the hosted browser past its idle time"


async def test_lightpanda_without_its_binary_says_how_to_get_it(tmp_path: Path) -> None:
    missing = await _call(tmp_path, "print(1)", GITHI_BROWSER_ENGINE="lightpanda", LIGHTPANDA_PATH="")

    assert missing["exit_code"] == 2 and "Lightpanda" in missing["error"], missing
