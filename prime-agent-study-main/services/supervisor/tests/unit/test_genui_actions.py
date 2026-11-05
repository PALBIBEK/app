"""A person's action on a surface the agent drew: taken only from a button the session drew."""

from __future__ import annotations

import itertools
import json
from typing import Any

import pytest

from supervisor.agent.events import AgentEvent, Envelope, ToolEnd, ToolStart
from supervisor.genui import CATALOG_ID
from supervisor.genui.actions import (
    ACTION_PREFIX,
    MAX_CONTEXT_CHARS,
    action_problem,
    action_text,
    drawings,
)

V = "v0.9"
BUTTON = {"id": "go", "component": "Button", "child": "label", "action": {"event": {"name": "chosen", "context": {"pick": {"path": "/pick"}}}}}


def create(surface: str, *components: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"version": V, "createSurface": {"surfaceId": surface, "catalogId": CATALOG_ID}},
        {"version": V, "updateComponents": {"surfaceId": surface, "components": list(components)}},
    ]


class Journal:
    """``show_ui`` calls as the journal holds them: a ToolStart, then its ToolEnd."""

    def __init__(self) -> None:
        self.envelopes: list[Envelope] = []
        self._seq = itertools.count(1)
        self._call = itertools.count(1)

    def _add(self, payload: AgentEvent) -> None:
        seq = next(self._seq)
        self.envelopes.append(Envelope("s", seq, f"e{seq}", seq, "run", None, payload))

    def show(self, messages: list[dict[str, Any]], *, ok: bool = True) -> int:
        """The seq of the call's end, the drawing's."""
        call = f"c{next(self._call)}"
        self._add(ToolStart(tool_call_id=call, tool_name="show_ui", args={"messages": messages}))
        self._add(ToolEnd(tool_call_id=call, tool_name="show_ui", result={"shown": []}, is_error=not ok))
        return self.envelopes[-1].seq


def action(**over: Any) -> dict[str, Any]:
    return {"name": "chosen", "surfaceId": "ask", "sourceComponentId": "go", "timestamp": "2026-11-05T10:00:00Z", "context": {"pick": ["a"]}} | over


def test_a_surface_is_what_its_calls_drew_last() -> None:
    journal = Journal()
    journal.show(create("ask", {"id": "root", "component": "Text", "text": "Which?"}))
    drawn = journal.show([{"version": V, "updateComponents": {"surfaceId": "ask", "components": [BUTTON]}}])
    journal.show(create("gone", {"id": "root", "component": "Text", "text": "x"}) + [{"version": V, "deleteSurface": {"surfaceId": "gone"}}])
    journal.show([{"version": V, "updateComponents": {"surfaceId": "ask", "components": [{"id": "go", "component": "Text", "text": "?"}]}}], ok=False)

    surfaces = drawings(journal.envelopes)

    assert list(surfaces) == ["ask"], "a deleted surface is gone"
    assert surfaces["ask"].seq == drawn, "the last call that drew it (a refused call draws nothing)"
    assert set(surfaces["ask"].components) == {"root", "go"} and surfaces["ask"].components["go"] == BUTTON


def test_drawing_a_surface_anew_starts_it_over() -> None:
    journal = Journal()
    journal.show(create("ask", BUTTON))
    journal.show(create("ask", {"id": "root", "component": "Text", "text": "Thanks"}))

    assert set(drawings(journal.envelopes)["ask"].components) == {"root"}


@pytest.fixture
def surfaces() -> dict[str, Any]:
    journal = Journal()
    journal.show(create("ask", {"id": "root", "component": "Column", "children": ["go"]}, BUTTON))
    return drawings(journal.envelopes)


def test_an_action_from_a_button_the_session_drew_is_taken(surfaces: dict[str, Any]) -> None:
    assert action_problem(action(), surfaces) is None


@pytest.mark.parametrize(
    ("sent", "said"),
    [
        ({"name": "chosen"}, "not an A2UI v0.9 action: 'surfaceId' is a required property"),
        (action(context="a"), "not an A2UI v0.9 action: context: 'a' is not of type 'object'"),
        (action(surfaceId="other"), "surface 'other' is not one this session shows"),
        (action(sourceComponentId="root"), "surface 'ask' has no button 'root'"),
        (action(sourceComponentId="nope"), "surface 'ask' has no button 'nope'"),
        (action(name="pay"), "button 'go' sends 'chosen', not 'pay'"),
        (action(context={"note": "x" * MAX_CONTEXT_CHARS}), f"over {MAX_CONTEXT_CHARS} characters"),
    ],
)
def test_any_other_action_is_refused_saying_why(surfaces: dict[str, Any], sent: Any, said: str) -> None:
    problem = action_problem(sent, surfaces)

    assert problem is not None and said in problem, problem


def test_the_session_is_told_the_action_without_its_time() -> None:
    told = action_text(action())
    again = action_text(action(timestamp="2026-11-05T10:00:05Z"))

    assert told == again, "the same click sent again is the same message"
    assert told.startswith(ACTION_PREFIX)
    assert json.loads(told.removeprefix(ACTION_PREFIX)) == {"name": "chosen", "surfaceId": "ask", "sourceComponentId": "go", "context": {"pick": ["a"]}}
