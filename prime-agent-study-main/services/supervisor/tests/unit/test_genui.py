"""A2UI the chat can draw: checked against the spec's own schemas, refused with what is wrong."""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from supervisor.genui import (
    CATALOG_ID,
    COMPONENTS,
    MAX_PROBLEMS,
    catalog_summary,
    problems,
    surface_of,
)
from supervisor.tools import InvalidArguments, default_registry
from supervisor.tools.builtin.show_ui import show_ui
from supervisor.tools.context import ToolContext

V = "v0.9"


def report(*components: dict[str, Any]) -> list[dict[str, Any]]:
    """A surface ``r`` made of ``components`` (the first is its root)."""
    return [
        {"version": V, "createSurface": {"surfaceId": "r", "catalogId": CATALOG_ID}},
        {"version": V, "updateComponents": {"surfaceId": "r", "components": list(components)}},
    ]


def components(*items: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"version": V, "updateComponents": {"surfaceId": "r", "components": list(items)}}]


SUMMARY = report(
    {"id": "root", "component": "Column", "children": ["title", "card"]},
    {"id": "title", "component": "Text", "text": "Price check", "variant": "h1"},
    {"id": "card", "component": "Card", "child": "body"},
    {"id": "body", "component": "Text", "text": {"path": "/body"}},
) + [{"version": V, "updateDataModel": {"surfaceId": "r", "path": "/", "value": {"body": "**3 shops**, cheapest £12"}}}]


def test_a_report_on_the_basic_catalog_is_valid() -> None:
    assert problems(SUMMARY) == []
    assert [surface_of(m) for m in SUMMARY] == ["r"] * 3


@pytest.mark.parametrize(
    ("messages", "said"),
    [
        ([{"surface": "r"}], "not an A2UI v0.9 message: one of createSurface"),
        ([{"version": "v0.8", "createSurface": {"surfaceId": "r", "catalogId": CATALOG_ID}}], "version is 'v0.8'"),
        ([{"version": V, "createSurface": {"surfaceId": "r"}}], "createSurface: 'catalogId' is a required property"),
        ([{"version": V, "createSurface": {"surfaceId": "r", "catalogId": "mine"}}], "use the basic catalog"),
        (components({"id": "root", "component": "Text"}), "component 'root' (Text): 'text' is a required property"),
        (components({"id": "root", "component": "Text", "text": "a", "variant": "huge"}), "variant: 'huge' is not one of"),
        (components({"id": "root", "component": "Text", "text": "a", "colour": "red"}), "('colour' was unexpected)"),
        (components({"id": "root", "component": "Chart"}), "component 'root' is 'Chart': this platform draws"),
    ],
)
def test_what_is_wrong_is_said_where_it_is(messages: list[dict[str, Any]], said: str) -> None:
    found = problems(messages)
    assert len(found) == 1 and said in found[0], found
    assert found[0].startswith("messages[0]: ")


ASK = report(
    {"id": "root", "component": "Column", "children": ["pick", "note", "go"]},
    {"id": "pick", "component": "ChoicePicker", "options": [{"label": "Shop A", "value": "a"}], "value": {"path": "/pick"}},
    {"id": "note", "component": "TextField", "label": "Anything else?", "value": {"path": "/note"}},
    {"id": "go", "component": "Button", "child": "go_label", "action": {"event": {"name": "chosen", "context": {"pick": {"path": "/pick"}}}}},
    {"id": "go_label", "component": "Text", "text": "Choose"},
)


def test_a_choice_with_a_button_that_sends_it_is_valid() -> None:
    assert problems(ASK) == []
    assert {"Button", "ChoicePicker", "TextField"} <= COMPONENTS


@pytest.mark.parametrize(
    ("component", "said"),
    [
        (
            {"id": "root", "component": "Button", "child": "x", "action": {"functionCall": {"call": "openUrl", "args": {"url": "https://a.example"}}}},
            "its action must be an event",
        ),
        ({"id": "root", "component": "Modal", "trigger": "a", "content": "b"}, "is 'Modal': this platform draws"),
    ],
)
def test_what_would_act_without_the_agent_is_refused(component: dict[str, Any], said: str) -> None:
    (found,) = problems(components(component))

    assert said in found, found


def test_problems_are_bounded() -> None:
    assert len(problems([{"bad": n} for n in range(20)])) == MAX_PROBLEMS


def test_the_tool_tells_the_model_the_components_it_checks() -> None:
    """The description lists the catalog by hand (a tool's description is its docstring); it
    must say what the vendored catalog holds."""
    description = default_registry()["show_ui"].definition()["description"]
    assert f"Components (* required): {catalog_summary()}." in description
    assert inspect.getdoc(show_ui).split("\n\n")[0] == description


@pytest.mark.asyncio
async def test_show_ui_accepts_a_surface_and_refuses_a_broken_one() -> None:
    run = default_registry()["show_ui"].run

    shown = await run({"messages": SUMMARY}, ToolContext())
    with pytest.raises(InvalidArguments, match=r"not A2UI the chat can draw: messages\[1\]: component 'root' \(Text\)"):
        await run({"messages": report({"id": "root", "component": "Text"})}, ToolContext())

    assert shown == {"shown": ["r"], "messages": 3}
