"""The browser code mode against a real headless Chromium, run as a sandbox runs it.

The workspace runs the call's command as a sandbox does (a shell in the workspace root
with a scrubbed environment, killed with everything it started on a stop), so these are
the runner's real paths: Chromium started once and
kept, reached again over CDP by the next call, the model's code run with Playwright's
``page`` and the helpers, and only what it prints returned. They need the sandbox's
runtime on this machine (``python3`` with Playwright and its Chromium): CI installs it.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import threading
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest

from supervisor.tools import default_registry
from supervisor.tools.context import ToolContext
from supervisor.tools.workspace import HostWorkspace

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        subprocess.run(["python3", "-c", "import playwright"], capture_output=True, check=False).returncode != 0,
        reason="no browser runtime here (python3 with Playwright and Chromium)",
    ),
]

_BROWSERS = {
    "PLAYWRIGHT_BROWSERS_PATH": os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or str(Path.home() / ".cache" / "ms-playwright"),
    **{k: v for k, v in os.environ.items() if k == "PLAYWRIGHT_HOST_PLATFORM_OVERRIDE"},
}
"""Where this machine keeps Playwright's browsers, and which build it uses: a workspace
command's HOME is the workspace, so Playwright's default would look there (a sandbox
image sets its own)."""
PAGE = "<title>Shop</title><h1>Paper towels</h1><ul><li>A $3</li><li>B $5</li></ul><input id=q>"
Call = Callable[..., "asyncio.Future[dict[str, Any]]"]


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

    yield run  # type: ignore[misc]
    _kill_browsers(home)


def _kill_browsers(home: Path) -> None:
    """The browsers the test started outlive its calls by design: stop them here."""
    subprocess.run(["pkill", "-f", "--", f"--user-data-dir={home}"], check=False)
    shutil.rmtree(home, ignore_errors=True)


async def test_a_call_runs_code_against_the_page_and_returns_what_it_prints(call: Call) -> None:
    first = await call(
        "# Opening the shop\n"
        f"page.set_content({PAGE!r})\n"
        "fill_input('#q', 'towels')\n"
        "print(page_info()['title'], js(\"document.querySelector('#q').value\"))\n"
        "print([li.inner_text() for li in page.locator('li').all()])"
    )

    assert first["exit_code"] == 0, first
    assert first["output"].splitlines() == ["Shop towels", "['A $3', 'B $5']"]
    assert first["browser_started"] is True
    assert first["workspace"] == ".githi/browser"


async def test_the_browser_outlives_the_call_and_the_variables_do_not(call: Call) -> None:
    await call(f"page.set_content({PAGE!r})\nkept = 1")

    second = await call("print(page.title())\nprint('kept' in globals())")

    assert second["output"].splitlines() == ["Shop", "False"]
    assert "browser_started" not in second, "the second call started another browser"


async def test_the_workspace_folder_carries_batches_and_helpers_between_calls(
    call: Call, tmp_path: Path
) -> None:
    await call(
        "import json, os\n"
        "open(os.path.join(WORKSPACE, 'agent_helpers.py'), 'w').write("
        "'def total(xs):\\n    return sum(xs)\\n\\ndef title():\\n    return page.title()\\n')\n"
        "json.dump([3, 5], open(os.path.join(os.environ['BROWSER_WORKSPACE'], 'prices.json'), 'w'))"
    )

    later = await call(
        "import json\npage.set_content('<title>Shop</title>')\n"
        "print(total(json.load(open(WORKSPACE + '/prices.json'))), title())"
    )

    assert later["output"] == "8 Shop", "a saved helper cannot reach the page"
    assert (tmp_path / "ws" / ".githi" / "browser" / "prices.json").exists(), "the batch is not in the workspace"


async def test_an_error_in_the_code_is_its_own_traceback(call: Call) -> None:
    failed = await call("print('before')\nitems = []\nitems[3]")

    assert failed["exit_code"] == 1
    assert failed["output"] == "before"
    assert failed["error"].endswith("IndexError: list index out of range\n")
    assert 'File "<browser_exec>", line 3' in failed["error"]
    assert "runner" not in failed["error"], "the runner's own frames reached the model"


async def test_a_playwright_error_points_at_the_code_not_at_playwright(call: Call) -> None:
    failed = await call("page.set_content('<p>no button</p>')\npage.click('#buy', timeout=300)")

    assert 'File "<browser_exec>", line 2' in failed["error"]
    assert "TimeoutError" in failed["error"]
    assert "/playwright/" not in failed["error"], "Playwright's internals reached the model"


async def test_a_dialog_is_answered_as_asked_and_never_passes_unseen(call: Call) -> None:
    """Playwright dismisses a dialog no handler takes, silently; here each one is listed,
    and ``answer_dialogs`` decides the answer (found when a checkout's confirm vanished)."""
    page = "page.set_content(\"<button onclick=\\\"document.title = confirm('Pay?') ? 'paid' : 'kept'\\\">Pay</button>\")\n"

    default = await call(page + "page.click('button')\nprint(page.title())")
    accepted = await call(page + "answer_dialogs('accept')\npage.click('button')\nprint(page.title())")

    assert default["output"] == "kept"
    assert default["dialogs"] == [{"type": "confirm", "message": "Pay?", "answer": "dismiss"}]
    assert accepted["output"] == "paid"
    assert accepted["dialogs"][0]["answer"] == "accept"


async def test_a_locator_that_names_nothing_fails_in_seconds(call: Call) -> None:
    failed = await call("page.set_content('<p>empty</p>')\npage.click('#missing')")

    assert "Timeout 5000ms exceeded" in failed["error"], failed["error"]


async def test_a_screenshot_is_saved_in_the_workspace(call: Call, tmp_path: Path) -> None:
    shot = await call(f"page.set_content({PAGE!r})\ncapture_screenshot()")

    path = Path(shot["output"])
    assert path.is_relative_to((tmp_path / "ws" / ".githi" / "browser" / "screenshots").resolve())
    assert path.read_bytes().startswith(b"\x89PNG")


async def test_named_sessions_get_browsers_of_their_own(call: Call) -> None:
    await call("page.set_content('<title>one</title>')", session="one")
    await call("page.set_content('<title>two</title>')", session="two")

    assert (await call("print(page.title())", session="one"))["output"] == "one"
    assert (await call("print(page.title())", session="two"))["output"] == "two"


async def test_a_stop_of_the_call_leaves_the_browser_running(call: Call) -> None:
    """A stop kills the call and everything it started, but not the browser: it runs in a
    session of its own, so the next call finds the page as it was."""
    await call(f"page.set_content({PAGE!r})")
    stop = threading.Event()
    asyncio.get_running_loop().call_later(1.0, stop.set)

    stopped = await call("import time\ntime.sleep(30)", stop=stop)
    after = await call("print(page.title())")

    assert stopped.get("stopped") is True, stopped
    assert after["output"] == "Shop"
    assert "browser_started" not in after, "the stop took the browser with it"
