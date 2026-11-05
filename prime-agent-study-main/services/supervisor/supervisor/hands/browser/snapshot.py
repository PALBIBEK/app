"""Page snapshots with element IDs, and acting by them (Stagehand's ``snapshot`` and
``run`` actions), for the browser code mode's runner.

A snapshot is the page's accessibility tree, trimmed where it is read rather than cut
afterwards: interactive elements and the structure around them (headings, rows, list
items, dialogs, forms), each line with an ID, labels beside a control kept with it, long
runs of alike siblings collapsed, and an open dialog shown alone. Iframes of the same
process are included, as frames of their own.

IDs are ``<frame>-<backend node id>``: Chrome's own id for the element, kept while the
element lives, so an ID never moves to another element when the page changes around it.
Each snapshot replaces the tab's IDs; acting by an ID that is not in the tab's latest
snapshot, after the tab navigated, or on an element that is gone, is refused with what to
do, rather than landing on whatever now sits there (found when IDs numbered by position
shifted under a click).

``act`` acts through Playwright, so widgets get the events they listen for, and settles
afterwards: a navigation the action started, the tab's network (the daemon's count), and
the DOM going quiet.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

INTERACTIVE = frozenset({
    "button", "link", "textbox", "searchbox", "checkbox", "radio", "combobox", "listbox", "option",
    "menuitem", "menuitemcheckbox", "menuitemradio", "tab", "switch", "slider", "spinbutton", "treeitem",
    "DisclosureTriangle",
})
SHOWN_AS = {"DisclosureTriangle": "disclosure"}
"""Chromium's names for roles that read better as ARIA's (a ``<summary>``: click it to open)."""
STRUCTURE = frozenset({
    "heading", "dialog", "alertdialog", "form", "table", "row", "list", "listitem", "navigation",
    "tabpanel", "menu", "tablist", "grid", "tree", "alert", "status", "Iframe",
})
TEXT = frozenset({"StaticText"})
DIALOGS = frozenset({"dialog", "alertdialog"})
STATES = ("checked", "selected", "expanded", "disabled", "pressed", "required")
NAME_CAP = 80
ALIKE_SHOWN = 3
"""Of a run of alike siblings (same role, name and shape), how many are shown."""
DEFAULT_MAX_LINES = 200
QUIET_WINDOW_MS = 400
"""The DOM counts as settled once nothing in it changed for this long (Stagehand's 400)."""
QUIET_POLL_MS = 100


class StaleSnapshot(Exception):
    """An ID that cannot be trusted any more: what to do is in the message."""


@dataclass
class Node:
    role: str
    name: str
    backend: int | None
    value: str | None = None
    states: dict[str, Any] = field(default_factory=dict)
    children: list[Node] = field(default_factory=list)
    labels_control: bool = False
    """A text beside a control (its label, a product's name by its button): shown."""

    @property
    def shown(self) -> bool:
        if self.role in INTERACTIVE or self.role in TEXT:
            return True
        return self.role in STRUCTURE and (bool(self.name) or self.role in ("table", "list", "form", "Iframe"))


def _tree(nodes: list[dict[str, Any]]) -> Node | None:
    """The CDP accessibility nodes of one frame as a tree, ignored nodes left out (their
    children joining the nearest kept ancestor)."""
    by_id = {n["nodeId"]: n for n in nodes}

    def build(raw: dict[str, Any]) -> list[Node]:
        kids = [k for child in raw.get("childIds", []) if (c := by_id.get(child)) for k in build(c)]
        if raw.get("ignored"):
            return kids
        role = str(raw.get("role", {}).get("value", ""))
        if role == "InlineTextBox":
            return []
        props = {p["name"]: p.get("value", {}).get("value") for p in raw.get("properties", [])}
        node = Node(
            role=role,
            name=" ".join(str(raw.get("name", {}).get("value") or "").split()),
            backend=raw.get("backendDOMNodeId"),
            value=raw.get("value", {}).get("value"),
            states={k: props[k] for k in STATES if k in props and props[k] not in (False, "false", None)},
            children=kids,
        )
        return [node]

    roots = build(nodes[0]) if nodes else []
    return roots[0] if roots else None


def _line(node: Node, frame: int) -> str:
    ident = f"[{frame}-{node.backend}] " if node.backend is not None and node.role not in TEXT else ""
    name = node.name if len(node.name) <= NAME_CAP else node.name[: NAME_CAP - 1] + "…"
    if node.role in TEXT:
        return f'"{name}"'
    text = f"{ident}{SHOWN_AS.get(node.role, node.role)}" + (f": {name}" if name else "")
    if node.value not in (None, "", node.name):
        text += f" = {str(node.value)[:40]}"
    text += "".join(f" [{k}]" if v in (True, "true") else f" [{k}={v}]" for k, v in node.states.items())
    return text


def _shape(node: Node) -> tuple[Any, ...]:
    return (node.role, node.name, tuple(_shape(c) for c in node.children if c.shown))


def _render(node: Node, frame: int, depth: int = 0, *, collapse: bool = True) -> Iterator[tuple[int, str, Node]]:
    """(depth, line, node) for every shown node; alike runs of siblings collapsed unless
    ``collapse`` is off (a search must find the one it hides). A text is shown only beside
    something interactive (a label), never as page prose."""
    if node.shown and not (node.role in TEXT and not node.labels_control):
        yield depth, _line(node, frame), node
        depth += 1
    if not collapse:
        for child in node.children:
            yield from _render(child, frame, depth, collapse=False)
        return
    run: list[Node] = []
    for child in [*node.children, None]:
        if child is not None and run and _shape(child) == _shape(run[0]):
            run.append(child)
            continue
        for alike in run[:ALIKE_SHOWN]:
            yield from _render(alike, frame, depth)
        if len(run) > ALIKE_SHOWN:
            yield depth, f"… {len(run) - ALIKE_SHOWN} more like the above", run[0]
        run = [child] if child is not None else []


def _mark_labels(node: Node) -> None:
    """Texts that are a control's siblings label it: keep them (and only them)."""
    has_control = any(c.role in INTERACTIVE for c in node.children)
    for child in node.children:
        child.labels_control = child.role in TEXT and has_control
        _mark_labels(child)


def _dialog(node: Node) -> Node | None:
    if node.role in DIALOGS:
        return node
    for child in node.children:
        if (found := _dialog(child)) is not None:
            return found
    return None


class Snapshots:
    """Snapshots of a tab and actions on their IDs; the tab's latest snapshot is kept in
    ``home`` (calls are separate processes) so a later call can act on its IDs."""

    def __init__(self, home: Path, settle: Callable[[Any], None]) -> None:
        self.home = home
        self.settle = settle

    # Snapshot ----------------------------------------------------------------

    def snapshot(self, page: Any, tab: str, *, contains: str | None = None, max_lines: int = DEFAULT_MAX_LINES) -> str:
        """The tab's trimmed tree as text; its IDs replace the tab's earlier ones."""
        lines: list[tuple[int, str, Node]] = []
        trees = self._frames(page)
        focus = next((d for _, tree in trees if (d := _dialog(tree)) is not None), None)
        shown = [(0, focus)] if focus is not None else trees
        for ordinal, root in shown:
            _mark_labels(root)
            if ordinal:
                lines.append((0, f"frame {ordinal}:", root))
            lines.extend(_render(root, ordinal, 1 if ordinal else 0, collapse=not contains))
        ids = [f"{o}-{n.backend}" for o, t in trees for n in _walk(t) if n.backend is not None]
        (self.home / f"snapshot-{tab}.json").write_text(json.dumps({"url": page.url, "ids": ids}))
        picked = _matching(lines, contains) if contains else lines
        header = f"{page.title()} — {page.url}" + (" (an open dialog; the rest of the page is behind it)" if focus else "")
        body = ["  " * d + text for d, text, _ in picked[:max_lines]]
        if len(picked) > max_lines:
            body.append(f"… {len(picked) - max_lines} more lines: snapshot(contains=...) narrows it")
        return "\n".join([header, *body])

    def _frames(self, page: Any) -> list[tuple[int, Node]]:
        """(ordinal, tree) of the main frame (0) and each iframe the page's process holds.
        An iframe of another site runs in a process of its own, out of reach here: its
        element says so, and how to act in it."""
        cdp = page.context.new_cdp_session(page)
        try:
            frame_ids = [f["frame"]["id"] for f in _frame_list(cdp.send("Page.getFrameTree")["frameTree"])]
            out: list[tuple[int, Node]] = []
            for ordinal, frame_id in enumerate(frame_ids):
                tree = _tree(cdp.send("Accessibility.getFullAXTree", {"frameId": frame_id})["nodes"])
                if tree is not None:
                    out.append((ordinal, tree))
            for _, tree in out:
                for node in _walk(tree):
                    if node.role == "Iframe" and node.backend is not None and _hosted_frame(cdp, node.backend) not in frame_ids:
                        node.name = OTHER_SITE
            return out
        finally:
            cdp.detach()

    # Act ---------------------------------------------------------------------

    def act(self, page: Any, tab: str, steps: tuple[tuple[Any, ...], ...]) -> None:
        """Run steps like ("click", "0-42"), ("fill", "0-7", "Ada"), ("select", "0-9", "Tech"),
        ("check", "0-3"), ("press", "0-7", "Enter"), every ID checked first; then settle."""
        locators = [self.locate(page, tab, step[1]) for step in steps]
        for (op, _, *args), locator in zip(steps, locators, strict=True):
            run = ACTIONS.get(op)
            if run is None:
                raise ValueError(f"unknown action {op!r}: one of {', '.join(ACTIONS)}")
            run(locator, *args)
        self.settle(page)

    def locate(self, page: Any, tab: str, gid: str) -> Any:
        """A Playwright locator for the element an ID of the tab's latest snapshot names;
        ``StaleSnapshot`` (with what to do) when it cannot be trusted."""
        state_file = self.home / f"snapshot-{tab}.json"
        if not state_file.exists():
            raise StaleSnapshot("no snapshot of this tab yet: call snapshot() first")
        state = json.loads(state_file.read_text())
        if page.url != state["url"]:
            raise StaleSnapshot("the tab navigated since its snapshot: call snapshot() again")
        if gid not in state["ids"]:
            raise StaleSnapshot(f"{gid} is not in this tab's latest snapshot: call snapshot() and use its IDs")
        backend = int(gid.split("-", 1)[1])
        mark = f"githi-{gid}"
        cdp = page.context.new_cdp_session(page)
        try:
            try:
                obj = cdp.send("DOM.resolveNode", {"backendNodeId": backend})["object"]["objectId"]
            except Exception as exc:
                raise StaleSnapshot(f"{gid} is gone from the page: call snapshot() again") from exc
            cdp.send(
                "Runtime.callFunctionOn",
                {"objectId": obj, "functionDeclaration": f"function() {{ this.setAttribute('data-githi', {json.dumps(mark)}) }}"},
            )
        finally:
            cdp.detach()
        selector = f"[data-githi={json.dumps(mark)}]"
        for candidate in page.frames:  # the mark is unique: whichever frame holds it
            if candidate.locator(selector).count():
                return candidate.locator(selector)
        raise StaleSnapshot(f"{gid} is gone from the page: call snapshot() again")


ACTIONS: dict[str, Callable[..., Any]] = {
    "click": lambda el: el.click(),
    "fill": lambda el, text: el.fill(text),
    "type": lambda el, text: el.press_sequentially(text),
    "press": lambda el, key: el.press(key),
    "select": lambda el, label: el.select_option(label=label),
    "check": lambda el: el.check(),
    "uncheck": lambda el: el.uncheck(),
    "hover": lambda el: el.hover(),
}


OTHER_SITE = "another site's frame, not read here: act in it with page.frame_locator(...)"


def _hosted_frame(cdp: Any, backend: int) -> str | None:
    """The id of the frame an iframe element hosts."""
    try:
        return cdp.send("DOM.describeNode", {"backendNodeId": backend})["node"].get("frameId")
    except Exception:  # noqa: BLE001 — the element went while we looked
        return None


def _walk(node: Node | None) -> Iterator[Node]:
    if node is None:
        return
    yield node
    for child in node.children:
        yield from _walk(child)


def _frame_list(tree: dict[str, Any]) -> Iterator[dict[str, Any]]:
    yield tree
    for child in tree.get("childFrames", []):
        yield from _frame_list(child)


def _matching(lines: list[tuple[int, str, Node]], needle: str) -> list[tuple[int, str, Node]]:
    """Lines that contain ``needle``, each with its group: the lines around it at its depth
    or deeper (a product's name brings its button), and the headings of its ancestors."""
    keep: set[int] = set()
    lowered = needle.lower()
    for i, (depth, text, _) in enumerate(lines):
        if lowered not in text.lower():
            continue
        start = i
        while start > 0 and lines[start - 1][0] >= depth and lines[start - 1][0] > 0:
            start -= 1
        end = i
        while end + 1 < len(lines) and lines[end + 1][0] >= depth:
            end += 1
        keep.update(range(start, end + 1))
        level = depth
        for j in range(start - 1, -1, -1):  # the ancestors, for where it sits
            if lines[j][0] < level:
                keep.add(j)
                level = lines[j][0]
    return [lines[i] for i in sorted(keep)]


def settle(page: Any, network_idle: Callable[[float], bool], timeout: float = 5.0) -> None:
    """After an action: whatever it started has finished (a navigation, a form's request,
    the requests a new page makes), then the DOM has been quiet for ``QUIET_WINDOW_MS``;
    at most ``timeout`` seconds. A navigation mid-wait replaces the document the check
    runs in, so the DOM check is retried in the new one."""
    deadline = time.monotonic() + timeout
    network_idle(timeout)
    while (left := deadline - time.monotonic()) > 0:
        try:
            page.wait_for_load_state("domcontentloaded", timeout=left * 1000)
            page.wait_for_function(QUIET_JS, arg=QUIET_WINDOW_MS, polling=QUIET_POLL_MS, timeout=left * 1000)
            return
        except Exception:  # noqa: BLE001 — the document went mid-check, or never settled in time
            time.sleep(0.05)


def quiet_wait(page: Any, ms: float) -> None:
    """``page.wait_for_timeout(ms)`` that ends once the page is quiet (loaded, and no DOM
    change for ``QUIET_WINDOW_MS``), never later than ``ms``: models overshoot fixed waits
    (an 8 s sleep returned in 0.6 s in LangChain's runs)."""
    if ms <= QUIET_WINDOW_MS:
        time.sleep(ms / 1000)
        return
    deadline = time.monotonic() + ms / 1000
    try:
        page.wait_for_function(QUIET_JS, arg=QUIET_WINDOW_MS, polling=QUIET_POLL_MS, timeout=ms)
    except Exception:  # noqa: BLE001 — not quiet in time, or the page went: sleep out the rest
        time.sleep(max(0.0, deadline - time.monotonic()))


QUIET_JS = """(windowMs) => {
  let state = document.__githiQuiet;
  if (!state) {
    state = { last: performance.now() };
    new MutationObserver(() => { state.last = performance.now(); })
      .observe(document, { subtree: true, childList: true, attributes: true, characterData: true });
    Object.defineProperty(document, "__githiQuiet", { value: state, configurable: true });
  }
  return document.readyState !== "loading" && performance.now() - state.last >= windowMs;
}"""
