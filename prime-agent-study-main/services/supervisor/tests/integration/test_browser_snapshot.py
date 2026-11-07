"""Snapshots with element IDs, and acting by them, against a real Chromium.

Each test is a failure the experiment met with a first prototype
(``harness/experiments/browser-code-mode``): IDs that moved under a click, an old ID that
landed on another element, 240 lines of "button: View" with no way to tell which one, and
reads that came before the page had settled."""

from __future__ import annotations

import time

import pytest

from tests.support.browser import Call, Site, needs_browser

pytestmark = [pytest.mark.asyncio, needs_browser]

SHOP = (
    "<title>Shop</title><h1>Shoes</h1><ul>"
    + "".join(f"<li><span>Runner {i}</span> <button onclick=\"document.title='picked {i}'\">View</button></li>" for i in range(60))
    + "</ul>"
)


def _id_of(snapshot: str, text: str) -> str:
    """The ID on the first element line of ``snapshot`` that mentions ``text``."""
    line = next(line for line in snapshot.splitlines() if text in line and "[" in line)
    return line[line.index("[") + 1 : line.index("]")]


async def test_a_label_beside_a_control_names_it(call: Call) -> None:
    """A product's name is kept beside its button, so the right "View" can be told apart."""
    shown = await call(f"page.set_content({SHOP!r})\nprint(snapshot(contains='Runner 42'))")

    lines = shown["output"].splitlines()
    assert any('"Runner 42"' in line for line in lines), shown["output"]
    view = next(line for line in lines[lines.index(next(x for x in lines if '"Runner 42"' in x)) :] if "button: View" in line)
    clicked = await call(f"act(('click', {view[view.index('[') + 1 : view.index(']')]!r}))\nprint(page.title())")

    assert clicked["output"] == "picked 42"


async def test_alike_siblings_are_collapsed(call: Call) -> None:
    shown = await call("page.set_content('<div>' + '<button>Add</button>' * 100 + '</div>')\nprint(snapshot())")

    assert shown["output"].count("button: Add") == 3
    assert "… 97 more like the above" in shown["output"]


async def test_a_search_finds_what_a_collapsed_run_hides(call: Call) -> None:
    """The benchmark's admin index: "Loans" sat in "… 3 more like the above", and
    snapshot(contains="Loan") found nothing."""
    models = "".join(f"<tr><th><a href=#{m}>{m}</a></th><td><a href=#add>Add</a></td></tr>" for m in ("Authors", "Books", "Branchs", "Loans", "Members", "Orders"))
    shown = await call(f"page.set_content({('<table><caption>LIB</caption>' + models + '</table>')!r})\nprint(snapshot())\nprint('---')\nprint(snapshot(contains='Loan'))")

    whole, found = shown["output"].split("---")
    assert "more like the above" in whole and "Loans" not in whole
    assert "link: Loans" in found, found


async def test_a_disclosure_is_shown_and_opens_by_its_id(call: Call) -> None:
    """The shop's inbox: messages in <details>, which the snapshot left out."""
    inbox = "<h1>Inbox</h1><details><summary>Your coupon</summary><p>Use <b>CODE-1</b></p></details>"
    shown = await call(f"page.set_content({inbox!r})\nprint(snapshot())")
    opened = await call(f"act(('click', {_id_of(shown['output'], 'Your coupon')!r}))\nprint(page.inner_text('details'))")

    assert "disclosure: Your coupon" in shown["output"], shown["output"]
    assert "CODE-1" in opened["output"]


async def test_an_id_is_a_locator_for_what_act_does_not_do(call: Call) -> None:
    """Choosing two authors in a multi-select (the benchmark's add-book form)."""
    form = "<label>Authors <select id=a multiple><option>Ada</option><option>Grace</option><option>Alan</option></select></label>"
    shown = await call(f"page.set_content({form!r})\nprint(snapshot())")
    box = _id_of(shown["output"], "listbox")
    chosen = await call(f"locate({box!r}).select_option(label=['Ada', 'Grace'])\nprint(locate({box!r}).evaluate('s => [...s.selectedOptions].map(o => o.text)'))")

    assert chosen["output"] == "['Ada', 'Grace']", chosen


async def test_an_id_stays_on_its_element_when_the_page_changes(call: Call) -> None:
    """Chrome's own node ids: an element added before the target does not move its ID."""
    first = await call("page.set_content('<button onclick=\"document.title=1\">one</button>')\nprint(snapshot())")
    one = _id_of(first["output"], "one")
    zero = '<button onclick="document.title=0">zero</button>'

    after = await call(
        f"page.evaluate('html => document.body.insertAdjacentHTML(\"afterbegin\", html)', {zero!r})\n"
        "snapshot()\n"
        f"act(('click', {one!r}))\nprint(page.title())"
    )

    assert after["output"] == "1", after


async def test_ids_from_before_a_navigation_or_an_older_snapshot_are_refused(call: Call) -> None:
    first = await call("page.set_content('<button>a</button>')\nprint(snapshot())")
    old = _id_of(first["output"], "a")

    navigated = await call(f"page.goto('about:blank#elsewhere')\nact(('click', {old!r}))")
    replaced = await call(f"page.set_content('<button>b</button>')\nsnapshot()\nact(('click', {old!r}))")

    assert "navigated since its snapshot" in navigated["error"]
    assert "not in this tab's latest snapshot" in replaced["error"]


async def test_an_element_that_is_gone_is_refused(call: Call) -> None:
    first = await call("page.set_content('<button id=b>gone soon</button>')\nprint(snapshot())")
    gone = _id_of(first["output"], "gone soon")

    refused = await call(f"js(\"document.getElementById('b').remove()\")\nact(('click', {gone!r}))")

    assert "is gone from the page" in refused["error"], refused["error"]


async def test_an_open_dialog_is_shown_alone(call: Call) -> None:
    shown = await call(
        f"page.set_content({SHOP!r} + '<div role=dialog aria-label=Size><button>EU 42</button></div>')\nprint(snapshot())"
    )

    assert "an open dialog" in shown["output"]
    assert "EU 42" in shown["output"] and "Runner" not in shown["output"]


async def test_an_iframe_is_a_frame_of_its_own_and_can_be_acted_in(call: Call) -> None:
    pay = '<input aria-label="Card"><button onclick="parent.document.title = document.querySelector(`input`).value">Pay</button>'
    shown = await call(
        f"page.evaluate('srcdoc => {{ const f = document.createElement(\"iframe\"); f.srcdoc = srcdoc; document.body.append(f) }}', {pay!r})\n"
        "page.wait_for_timeout(300)\nprint(snapshot())"
    )

    card, button = _id_of(shown["output"], "Card"), _id_of(shown["output"], "Pay")
    paid = await call(f"act(('fill', {card!r}, '4242'), ('click', {button!r}))\nprint(page.title())")

    assert "frame 1:" in shown["output"]
    assert paid["output"] == "4242"


async def test_a_password_is_never_shown(call: Call) -> None:
    shown = await call("page.set_content('<input type=password aria-label=Password value=hunter2>')\nprint(snapshot())")

    assert "hunter2" not in shown["output"]


async def test_acting_on_a_stateful_widget_sends_its_events(call: Call) -> None:
    """A select whose page listens for change (as Django's two-box picker does): acting
    through Playwright fires it; setting the value from script would not."""
    page = (
        "<select aria-label=Authors><option>Ada</option><option>Alan</option></select><p id=chosen></p>"
        "<script>document.querySelector('select').onchange = e => document.getElementById('chosen').textContent = e.target.value</script>"
    )
    shown = await call(f"page.set_content({page!r})\nprint(snapshot())")

    acted = await call(f"act(('select', {_id_of(shown['output'], 'Authors')!r}, 'Alan'))\nprint(js(\"document.getElementById('chosen').textContent\"))")

    assert acted["output"] == "Alan"


async def test_act_settles_before_the_next_read(call: Call) -> None:
    """The click starts a late navigation: the read right after ``act`` sees the new page
    (the experiment's first read after a save came before it, and showed nothing)."""
    page = "<button onclick=\"setTimeout(() => location.href = 'about:blank#saved', 300)\">Save</button>"
    shown = await call(f"page.set_content({page!r})\nprint(snapshot())")

    acted = await call(f"act(('click', {_id_of(shown['output'], 'Save')!r}))\nprint(page.url)")

    assert acted["output"].endswith("#saved"), acted


async def test_a_long_fixed_wait_ends_once_the_page_is_quiet(call: Call) -> None:
    started = time.monotonic()
    quiet = await call("page.set_content('<p>still</p>')\npage.wait_for_timeout(8000)\nprint('done')")
    quick = time.monotonic() - started

    busy = await call(
        "page.set_content('<p id=p></p><script>let n = 0; const t = setInterval(() => { p.textContent = n++; if (n > 15) clearInterval(t) }, 100)</script>')\n"
        "import time; s = time.monotonic(); page.wait_for_timeout(8000); print(round(time.monotonic() - s, 1))"
    )

    assert quiet["output"] == "done" and quick < 4, f"an 8 s wait on a quiet page took {quick:.1f}s"
    assert 1.4 <= float(busy["output"]) < 4, "the wait ended while the page was still changing"


async def test_another_sites_frame_is_read_and_acted_in_by_its_ids(call: Call, site: Site) -> None:
    """A frame of another site (a payment form, a login widget) runs in a process of its own:
    the snapshot reads it through its own session, names the iframe by its frame, and an ID
    in it acts there, in that process (an element id is only unique within one)."""
    site.pages["/pay"] = ("<button onclick=\"document.body.append('paid')\">Pay</button>", 0)
    site.pages["/shop"] = (f"<title>shop</title><button>Pay</button><iframe src={site.url('/pay', host='localhost')!r}></iframe>", 0)

    shown = await call(f"page.goto({site.url('/shop')!r})\npage.wait_for_timeout(500)\nprint(snapshot())")
    lines = shown["output"].splitlines()
    inner = next(line for line in lines[lines.index("frame 1:"):] if "button: Pay" in line)
    paid = await call(f"act(('click', {inner[inner.index('[') + 1 : inner.index(']')]!r}))\nprint(page.frame_locator('iframe').locator('body').inner_text())")

    assert "Iframe: frame 1" in shown["output"] and "frame 1:" in lines, shown["output"]
    assert inner.strip().startswith("[1-"), "the frame's button is numbered by its frame"
    assert paid["output"].endswith("paid"), paid


async def test_another_sites_frame_is_still_reachable_through_playwright(call: Call, site: Site) -> None:
    site.pages["/pay"] = ("<button onclick=\"document.body.append('paid')\">Pay</button>", 0)
    site.pages["/shop"] = (f"<title>shop</title><iframe src={site.url('/pay', host='localhost')!r}></iframe>", 0)

    await call(f"page.goto({site.url('/shop')!r})")
    paid = await call("frame = page.frame_locator('iframe')\nframe.get_by_role('button', name='Pay').click()\nprint(frame.locator('body').inner_text())")

    assert paid["output"].endswith("paid")
