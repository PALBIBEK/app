"""The browser code mode against a real headless Chromium, run as a sandbox runs it
(``tests.support.browser``): the browser kept between calls and reached again over CDP,
the model's code run with Playwright's ``page`` and the helpers, and only what it prints
returned."""

from __future__ import annotations

import asyncio
import base64
import importlib
import threading
from pathlib import Path

import pytest

from tests.support.browser import Call, Tool, needs_browser

pytestmark = [pytest.mark.asyncio, needs_browser]

PAGE = "<title>Shop</title><h1>Paper towels</h1><ul><li>A $3</li><li>B $5</li></ul><input id=q>"


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


async def test_code_that_does_not_parse_is_pointed_at_its_line(call: Call) -> None:
    failed = await call("print('fine')\nprint('missing paren'")

    assert "SyntaxError" in failed["error"] and "line 2" in failed["error"], failed["error"]
    assert "runner" not in failed["error"], "the runner's own frame reached the model"


async def test_a_playwright_error_points_at_the_code_not_at_playwright(call: Call) -> None:
    failed = await call("page.set_content('<p>no button</p>')\npage.click('#buy', timeout=300)")

    assert 'File "<browser_exec>", line 2' in failed["error"]
    assert "TimeoutError" in failed["error"]
    assert "/playwright/" not in failed["error"], "Playwright's internals reached the model"


async def test_a_dialog_is_answered_as_asked_and_never_passes_unseen(call: Call, tool: Tool) -> None:
    """Playwright dismisses a dialog no handler takes, silently; here each one is listed.
    ``answer_dialogs`` decides the answer for the rest of a call; a dialog nothing answered
    is held, the call ends with it pending, and ``browser_dialog`` answers it (found when a
    checkout's confirm vanished)."""
    page = "page.set_content(\"<button onclick=\\\"document.title = confirm('Pay?') ? 'paid' : 'kept'\\\">Pay</button>\")\n"

    held = await call(page + "page.click('button')\nprint(page.title())")
    dismissed = await tool("browser_dialog", action="dismiss")
    kept = await call("print(page.title())")
    accepted = await call(page + "answer_dialogs('accept')\npage.click('button')\nprint(page.title())")

    assert held["blocked_by_dialog"] is True and held["output"] == "", held
    (pending,) = held["pending_dialogs"]
    assert {k: pending[k] for k in ("type", "message")} == {"type": "confirm", "message": "Pay?"}
    assert dismissed["success"] is True and dismissed["action"] == "dismiss"
    assert kept["output"] == "kept"
    assert accepted["output"] == "paid"
    (taken,) = [e for e in accepted["events"] if e["kind"] == "dialog"]
    assert taken["answer"] == "accept" and taken["by"] == "auto_policy"


async def test_a_repeated_event_is_reported_once_with_how_often(call: Call) -> None:
    """The benchmark's admin logged one console error per page: 21 pages, 20 copies."""
    noisy = await call("for i in range(5):\n    page.set_content(\"<script>console.error('same')</script><p>\" + str(i))\n    page.wait_for_timeout(50)")

    same = [e for e in noisy["events"] if e.get("text") == "same"]
    assert len(same) == 1 and same[0]["times"] == 5, noisy["events"]


async def test_a_locator_that_names_nothing_fails_in_seconds(call: Call) -> None:
    failed = await call("page.set_content('<p>empty</p>')\npage.click('#missing')")

    assert "Timeout 5000ms exceeded" in failed["error"], failed["error"]


async def test_a_screenshot_is_saved_in_the_workspace(call: Call, tmp_path: Path) -> None:
    shot = await call(f"page.set_content({PAGE!r})\ncapture_screenshot()")

    path = Path(shot["output"])
    assert path.is_relative_to((tmp_path / "ws" / ".githi" / "browser" / "screenshots").resolve())
    assert path.read_bytes().startswith(b"\x89PNG")


async def test_the_calls_last_capture_is_attached_for_a_model_that_sees(
    call: Call, tmp_path: Path
) -> None:
    """Hermes's native screenshot attach: a call that captures and shows nothing itself
    gets its last capture back as an image, so the model never needs another tool to look."""
    shot = await call(
        f"page.set_content({PAGE!r})\ncapture_screenshot()\npage.set_content('<h1>later</h1>')\ncapture_screenshot()"
    )

    (image,) = shot["images"]
    assert image["mime_type"] == "image/png" and base64.b64decode(image["data"]).startswith(
        b"\x89PNG"
    )
    (shown,) = shot["screenshots"]
    assert str(tmp_path / "ws" / shown) == str(Path(shot["output"].splitlines()[-1])), (
        "the last capture"
    )


async def test_what_the_code_shows_itself_replaces_the_attached_capture(call: Call) -> None:
    shot = await call(f"page.set_content({PAGE!r})\ncapture_screenshot()\nshow_screenshot()")

    assert [i["mime_type"] for i in shot["images"]] == ["image/jpeg"], (
        "only the screenshot the code showed"
    )


NOISE = (
    "<body style=margin:0><canvas id=c width=1280 height=720></canvas><script>"
    "const x=c.getContext('2d'),d=x.createImageData(1280,720);"
    "for(let i=0;i<d.data.length;i++)d.data[i]=i%4==3?255:Math.random()*256;x.putImageData(d,0,0)</script>"
)
"""A page whose PNG is megabytes: noise does not compress."""


async def test_a_capture_too_large_to_attach_is_attached_as_a_jpeg_copy(call: Call) -> None:
    """Hermes re-encodes a large capture as a JPEG to attach; the capture itself is kept."""
    shot = await call(f"page.set_content({NOISE!r})\ncapture_screenshot()")

    assert Path(shot["output"]).read_bytes().startswith(b"\x89PNG"), "the capture stays a PNG"
    (image,) = shot["images"]
    assert image["mime_type"] == "image/jpeg" and base64.b64decode(image["data"]).startswith(
        b"\xff\xd8"
    )
    assert shot["screenshots"][0].startswith(".githi/browser/screenshots/attached-")


async def test_a_capture_outside_the_workspace_is_attached_from_a_copy_in_it(
    call: Call, tmp_path: Path
) -> None:
    outside = tmp_path / "elsewhere.png"

    shot = await call(f"page.set_content({PAGE!r})\ncapture_screenshot({str(outside)!r})")

    assert outside.read_bytes().startswith(b"\x89PNG")
    assert (
        shot["screenshots"][0].startswith(".githi/browser/screenshots/attached-") and shot["images"]
    )


async def test_max_dim_bounds_the_longer_side_of_a_capture(call: Call) -> None:
    shot = await call(
        "page.set_viewport_size({'width': 1200, 'height': 600})\n"
        f"page.set_content({PAGE!r})\n"
        "import struct\n"
        "path = capture_screenshot(max_dim=600)\n"
        "w, h = struct.unpack('>II', open(path, 'rb').read()[16:24])\n"
        "print(w, h)"
    )

    assert shot["output"].splitlines()[-1] == "600 300", shot


async def test_a_new_tab_is_brought_to_the_front(call: Call) -> None:
    shot = await call("new_tab()\nprint(js('document.visibilityState'))")

    assert shot["output"] == "visible", shot


async def test_a_shown_screenshot_comes_back_with_the_result_as_an_image(call: Call, tmp_path: Path) -> None:
    shown = await call(f"page.set_content({PAGE!r})\nshow_screenshot()\npage.set_content('<h1>later</h1>')\nshow_screenshot()\nshow_screenshot()")

    (image,) = shown["images"][-1:]
    data = base64.b64decode(image["data"])
    assert image["mime_type"] == "image/jpeg" and data.startswith(b"\xff\xd8"), "a JPEG"
    assert len(shown["images"]) == len(shown["screenshots"]) == 2, "the latest two of three"
    assert (tmp_path / "ws" / shown["screenshots"][-1]).read_bytes() == data
    assert len(data) < 100 * 1024, "a viewport's JPEG is small enough to show on every call"


async def test_a_screenshot_too_large_to_show_stays_a_file(call: Call, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(importlib.import_module("supervisor.tools.builtin.browser_exec"), "MAX_SHOWN_BYTES", 1024)

    shown = await call(f"page.set_content({PAGE!r})\nshow_screenshot(full=True)")

    assert "images" not in shown, "over the bound"
    assert shown["not_shown"][0].startswith(shown["screenshots"][0]) and "show the viewport" in shown["not_shown"][0]


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
