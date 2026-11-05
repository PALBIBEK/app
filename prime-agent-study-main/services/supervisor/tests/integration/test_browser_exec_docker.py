"""The browser code mode in a real ``local_docker`` sandbox, as sessions get one: the
production container limits (512 MB, no network, no capabilities, 512 processes), the
runtime shipped by the tool, the browser and its daemon inside the container.

Needs a Docker daemon and an image with Python, Playwright and Chromium, named by
``SUPERVISOR_TEST_BROWSER_IMAGE`` (the shipped profile's Playwright image, or one built
from a machine that has them); skipped otherwise."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any

import pytest

from supervisor.sandbox.providers.local_docker import LocalDockerProvider
from supervisor.tools import default_registry
from supervisor.tools.context import ToolContext
from supervisor.tools.workspace import SandboxWorkspace

IMAGE = os.environ.get("SUPERVISOR_TEST_BROWSER_IMAGE", "")
pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(not IMAGE, reason="no browser sandbox image to run")]


@pytest.fixture
async def sandbox() -> AsyncIterator[SandboxWorkspace]:
    box = await LocalDockerProvider().create(image=IMAGE, workdir="/workspace")
    try:
        yield SandboxWorkspace(box)
    finally:
        await box.destroy()


async def _call(workspace: SandboxWorkspace, code: str) -> dict[str, Any]:
    ctx = ToolContext(bound_workspace=workspace, timeout_s=90)
    return dict(await default_registry()["browser_exec"].run({"code": code}, ctx))


async def test_the_browser_runs_inside_the_sandbox_under_its_limits(sandbox: SandboxWorkspace) -> None:
    first = await _call(
        sandbox,
        "page.set_content('<title>boxed</title><button onclick=\"document.title = confirm(1)\">b</button>')\n"
        "answer_dialogs('accept'); page.click('button')\n"
        "print(page.title())",
    )
    second = await _call(sandbox, "print(page.title(), open('/proc/1/cgroup').read() != '')")
    shown = await _call(sandbox, "print(snapshot())")

    assert first["exit_code"] == 0, first
    assert first["output"] == "true" and first["browser_started"] is True
    assert second["output"] == "true True" and "browser_started" not in second
    assert "button: b" in shown["output"], shown


async def test_the_sandbox_reaches_no_network(sandbox: SandboxWorkspace) -> None:
    """The profile's network is none: a page on the open web fails to load, as it should."""
    failed = await _call(sandbox, "page.goto('http://example.com', timeout=5000)")

    assert failed["exit_code"] == 1
    assert "net::ERR" in failed["error"], failed["error"]
