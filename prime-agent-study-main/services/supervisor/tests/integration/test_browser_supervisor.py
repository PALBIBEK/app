"""The browser daemon's CDP supervisor (``supervisor.hands.browser.cdp_supervisor``), and
``browser_dialog`` and ``browser_cdp`` through it, against a real Chromium run as a
sandbox runs it.

Hermes's ``tests/tools/test_browser_supervisor.py``, through this harness's tools: these
are the tests that show the CDP wire protocol works (a mock can only show the paths we
thought to model). Each fires a dialog or builds frames in a page, then reads and answers
them the way the agent does.
"""

from __future__ import annotations

import json

import pytest

from tests.support.browser import Call, Site, Tool, needs_browser

pytestmark = [pytest.mark.asyncio, needs_browser]

PAGE = """<title>Supervisor pytest</title><h1>Supervisor pytest</h1>
<iframe id="inner" srcdoc="<body><h2>frame-marker</h2></body>" width="400" height="100"></iframe>"""


def _fire(expression: str) -> str:
    """Code that loads the test page, then runs ``expression`` in it (after a beat, so the
    call ends before the dialog it opens)."""
    return f"page.set_content({PAGE!r})\njs({expression!r})"


async def test_the_supervisor_starts_and_knows_the_top_frame(call: Call) -> None:
    shown = await call(_fire("void 0") + "\nimport json; print(json.dumps(frames()))")

    tree = json.loads(shown["output"])
    assert tree["top"] is not None and tree["top"]["is_oopif"] is False, tree
    assert any(not child["is_oopif"] for child in tree["children"]), (
        "the srcdoc iframe is a frame of the page's process"
    )
    assert "pending_dialogs" not in shown


async def test_a_main_frame_alert_is_detected_and_dismissed(call: Call, tool: Tool) -> None:
    fired = await call(
        _fire("setTimeout(() => alert('PYTEST-MAIN-ALERT'), 50)") + "\nimport time; time.sleep(0.5)"
    )
    answered = await tool("browser_dialog", action="dismiss")
    again = await tool("browser_dialog", action="dismiss")

    (dialog,) = fired["pending_dialogs"]
    assert dialog["type"] == "alert" and "PYTEST-MAIN-ALERT" in dialog["message"]
    assert answered["success"] is True
    assert again == {"success": False, "error": "no dialog is currently open"}


async def test_an_iframe_contentwindow_alert_is_detected(call: Call, tool: Tool) -> None:
    """An alert fired from inside a same-origin iframe surfaces too."""
    fired = await call(
        _fire(
            "setTimeout(() => document.querySelector('#inner').contentWindow.alert('PYTEST-IFRAME'), 50)"
        )
        + "\nimport time; time.sleep(0.5)"
    )
    answered = await tool("browser_dialog", action="accept")

    assert any("PYTEST-IFRAME" in d["message"] for d in fired["pending_dialogs"]), fired
    assert answered["success"] is True


async def test_a_prompt_gets_the_agents_text_back(call: Call, tool: Tool) -> None:
    fired = await call(
        _fire(
            "setTimeout(() => { window.__promptResult = prompt('give me a token', 'default-x'); }, 50)"
        )
        + "\nimport time; time.sleep(0.5)"
    )
    answered = await tool("browser_dialog", action="accept", prompt_text="PYTEST-PROMPT-REPLY")
    read = await call("print(js('window.__promptResult'))")

    (dialog,) = fired["pending_dialogs"]
    assert dialog["type"] == "prompt" and dialog["default_prompt"] == "default-x"
    assert answered["success"] is True and answered["dialog"]["id"] == dialog["id"]
    assert read["output"] == "PYTEST-PROMPT-REPLY"


async def test_answer_dialogs_answers_a_prompt_as_it_opens(call: Call) -> None:
    answered = await call(
        _fire("void 0") + "\nanswer_dialogs('accept', 'typed')\nprint(js(\"prompt('name?')\"))"
    )

    assert answered["output"] == "typed", answered
    assert "pending_dialogs" not in answered


async def test_with_two_dialogs_open_the_answer_names_one(
    call: Call, tool: Tool, site: Site
) -> None:
    """A dialog per tab, both held: an answer without ``dialog_id`` lists the candidates."""
    site.pages["/a"] = ("<title>a</title><script>setTimeout(() => alert('first'), 100)</script>", 0)
    site.pages["/b"] = (
        "<title>b</title><script>setTimeout(() => alert('second'), 100)</script>",
        0,
    )
    fired = await call(
        f"page.goto({site.url('/a')!r})\nnew_tab({site.url('/b')!r})\nimport time; time.sleep(0.4)"
    )
    ids = sorted(d["id"] for d in fired["pending_dialogs"])

    ambiguous = await tool("browser_dialog", action="dismiss")
    first = await tool("browser_dialog", action="dismiss", dialog_id=ids[0])
    second = await tool("browser_dialog", action="dismiss")

    assert len(ids) == 2, fired
    assert ambiguous["success"] is False and all(i in ambiguous["error"] for i in ids), ambiguous
    assert first["success"] is True and second["success"] is True


async def test_an_unserializable_value_comes_back_as_its_description(
    call: Call, tool: Tool
) -> None:
    await call(_fire("void 0"))
    tabs = await call("print(list_tabs()[0]['id'])")

    out = await tool(
        "browser_cdp",
        method="Runtime.evaluate",
        params={"expression": "Infinity", "returnByValue": True},
        target_id=tabs["output"],
    )

    assert out["success"] is True and out["target_id"] == tabs["output"]
    assert out["result"]["result"]["unserializableValue"] == "Infinity", out


async def test_browser_cdp_evaluates_inside_another_sites_frame(
    call: Call, tool: Tool, site: Site
) -> None:
    """The top page's Runtime.evaluate cannot reach an iframe of another site: its own
    session, which the supervisor attached, can."""
    site.pages["/pay"] = ("<title>pay</title><h1>card form</h1>", 0)
    site.pages["/shop"] = (
        f"<title>shop</title><iframe src={site.url('/pay', host='localhost')!r}></iframe>",
        0,
    )
    shown = await call(
        f"page.goto({site.url('/shop')!r})\npage.wait_for_timeout(500)\nimport json; print(json.dumps(frames()))"
    )
    tree = json.loads(shown["output"])
    (oopif,) = [f for f in tree["children"] if f["is_oopif"]]

    inside = await tool(
        "browser_cdp",
        method="Runtime.evaluate",
        params={"expression": "document.querySelector('h1').textContent", "returnByValue": True},
        frame_id=oopif["frame_id"],
    )
    same_site = await tool(
        "browser_cdp",
        method="Runtime.evaluate",
        params={"expression": "1"},
        frame_id=tree["top"]["frame_id"],
    )

    assert "localhost" in oopif["url"], oopif
    assert inside["success"] is True and inside["result"]["result"]["value"] == "card form", inside
    assert same_site["success"] is False and "not an out-of-process iframe" in same_site["error"]


async def test_browser_cdp_at_browser_level_lists_the_tabs(call: Call, tool: Tool) -> None:
    await call("page.set_content('<title>listed</title>')")

    out = await tool("browser_cdp", method="Target.getTargets")

    assert out["success"] is True
    assert any(t.get("title") == "listed" for t in out["result"]["targetInfos"]), out


async def test_browser_cdp_says_why_a_command_failed(call: Call, tool: Tool) -> None:
    await call("page.set_content('<p>x</p>')")

    out = await tool("browser_cdp", method="Nonexistent.method")

    assert out["success"] is False and "wasn't found" in out["error"], out
    assert out["cdp_docs"].startswith("https://chromedevtools.github.io/")


async def test_with_no_browser_open_the_tools_say_so_and_start_none(tool: Tool) -> None:
    dialog = await tool("browser_dialog", action="accept")
    cdp = await tool("browser_cdp", method="Target.getTargets")

    assert dialog == {
        "success": False,
        "error": "no browser is open: the agent has not used one in this sandbox",
    }
    assert cdp["success"] is False and "no browser is open" in cdp["error"]
