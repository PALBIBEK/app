"""Our journal as AG-UI events: every event the official SDK's own, and as clients read it."""

from __future__ import annotations

import itertools
import json
from typing import Any

import pydantic
import pytest
from ag_ui.core import BaseEvent, Event
from ag_ui.encoder import EventEncoder

from supervisor.agent.events import (
    AgentEvent,
    ApprovalRequired,
    Envelope,
    ErrorEvent,
    MessageEnd,
    RunEnd,
    RunStart,
    SubagentEnd,
    SubagentStart,
    ToolEnd,
    ToolStart,
    TurnEnd,
    TurnStart,
    UnknownEvent,
)
from supervisor.agui import A2UI_ACTIVITY, A2UI_OPERATIONS, TASK_STATE, AgUiTranslator
from supervisor.genui import CATALOG_ID
from supervisor.journal.partials import Partial

SID, RUN = "s1", "run-1"
EVENTS = pydantic.TypeAdapter(Event)


class Journal:
    """Envelopes of one session, numbered as the journal numbers them."""

    def __init__(self) -> None:
        self._seq = itertools.count()

    def __call__(self, payload: AgentEvent, *, run: str | None = RUN) -> Envelope:
        seq = next(self._seq)
        return Envelope(SID, seq, f"e{seq}", 1_000 + seq, run, None, payload)


def translated(*items: Envelope | Partial, translator: AgUiTranslator | None = None) -> list[dict[str, Any]]:
    """``items`` through one translator, each event encoded and parsed back by the SDK, as a
    client reads it (camelCase JSON)."""
    translator = translator or AgUiTranslator(SID)
    out: list[dict[str, Any]] = []
    for item in items:
        events = translator.envelope(item) if isinstance(item, Envelope) else translator.partial(item)
        out += [_as_read(e) for e in events]
    return out


def _as_read(event: BaseEvent) -> dict[str, Any]:
    wire = EventEncoder().encode(event).removeprefix("data: ").strip()
    EVENTS.validate_json(wire)  # an event the protocol's own models accept
    return json.loads(wire)


def types(events: list[dict[str, Any]]) -> list[str]:
    return [e["type"] for e in events]


def assistant(*parts: dict[str, Any]) -> MessageEnd:
    return MessageEnd(role="assistant", content=list(parts))


def test_a_run_with_a_reply_and_a_tool_call() -> None:
    j = Journal()
    call = {"type": "tool_call", "id": "c1", "name": "read", "arguments": {"path": "a.py"}}

    events = translated(
        j(RunStart()),
        j(TurnStart()),
        j(MessageEnd(role="user", content=[{"type": "text", "text": "read a.py"}])),
        j(assistant({"type": "text", "text": "Reading it."}, call)),
        j(ToolStart("c1", "read", {"path": "a.py"})),
        j(ToolEnd("c1", "read", {"ok": True, "payload": "print(1)"})),
        j(TurnEnd(stop_reason="stop")),
        j(RunEnd(messages=[])),
    )

    assert types(events) == [
        "RUN_STARTED",
        "TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_END",  # the user's message
        "TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_END",  # the reply
        "TOOL_CALL_START", "TOOL_CALL_ARGS", "TOOL_CALL_END",
        "TOOL_CALL_RESULT",
        "RUN_FINISHED",
    ]  # fmt: skip
    started, user = events[0], events[1]
    assert (started["threadId"], started["runId"]) == (SID, RUN)
    assert user["role"] == "user" and events[2]["delta"] == "read a.py"
    assert events[7] == {"type": "TOOL_CALL_START", "timestamp": 1003, "toolCallId": "c1", "toolCallName": "read", "parentMessageId": "e3"}
    assert json.loads(events[8]["delta"]) == {"path": "a.py"}, "ToolStart does not announce the call again"
    assert events[10]["toolCallId"] == "c1" and events[10]["content"] == "print(1)"
    assert "outcome" not in events[-1]


def test_a_streamed_reply_ends_with_what_was_written_and_is_not_sent_twice() -> None:
    j, translator = Journal(), AgUiTranslator(SID)
    grow = [Partial(RUN, 0, text, "", n) for n, text in enumerate(["Hel", "Hello, wo", "Hello, wo"])]

    events = translated(j(RunStart()), *grow, j(assistant({"type": "text", "text": "Hello, world"})), translator=translator)

    assert types(events) == ["RUN_STARTED", "TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_END"]
    assert "".join(e["delta"] for e in events if e["type"] == "TEXT_MESSAGE_CONTENT") == "Hello, world"
    assert {e["messageId"] for e in events[1:]} == {f"{RUN}:0"}, "one message, streamed then ended"


def test_a_written_reply_unlike_the_stream_closes_it_and_is_said_whole() -> None:
    j = Journal()

    events = translated(j(RunStart()), Partial(RUN, 0, "Draft", "", 1), j(assistant({"type": "text", "text": "Final"})))

    assert types(events)[3:] == ["TEXT_MESSAGE_END", "TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_END"]
    assert events[-2]["delta"] == "Final"


def test_reasoning_a_person_may_read_is_shown_and_signed_or_redacted_reasoning_is_not() -> None:
    j = Journal()
    shown = {"type": "reasoning", "text": "Check the tests first.", "signature": "S"}
    hidden = {"type": "reasoning", "text": "", "signature": "opaque", "redacted": True}

    events = translated(j(assistant(shown, hidden, {"type": "text", "text": "Done."})))

    assert types(events)[:5] == ["REASONING_START", "REASONING_MESSAGE_START", "REASONING_MESSAGE_CONTENT", "REASONING_MESSAGE_END", "REASONING_END"]
    assert events[2]["delta"] == "Check the tests first."
    assert "opaque" not in json.dumps(events)


@pytest.mark.parametrize(
    ("stop", "error", "ending"),
    [
        ("error", ErrorEvent("ModelGatewayError", retryable=False, detail="the model is down"), {"type": "RUN_ERROR", "message": "the model is down", "code": "ModelGatewayError"}),
        ("timeout", None, {"type": "RUN_ERROR", "message": "the run ended: timeout", "code": "timeout"}),
        ("aborted", None, {"type": "RUN_FINISHED", "threadId": SID, "runId": RUN, "outcome": {"type": "cancelled"}}),
    ],
)
def test_how_a_run_ended(stop: str, error: ErrorEvent | None, ending: dict[str, Any]) -> None:
    j = Journal()
    journal = [j(RunStart()), *([j(error)] if error else []), j(TurnEnd(stop_reason=stop)), j(RunEnd(messages=[]))]

    *_, last = translated(*journal)

    assert {k: v for k, v in last.items() if k != "timestamp"} == ending


def test_a_tool_result_carries_its_images() -> None:
    j = Journal()
    shot = {"mime_type": "image/jpeg", "data": "/9j/AAAA"}
    result = {"ok": True, "payload": {"output": "seen", "images": [shot]}}

    (event,) = translated(j(ToolEnd("c1", "browser_exec", result)))

    text, image = event["content"]
    assert text["type"] == "text" and "seen" in text["text"] and shot["data"] not in text["text"]
    assert image == {"type": "image", "source": {"type": "data", "value": shot["data"], "mimeType": "image/jpeg"}}


def _show(call_id: str, *messages: dict[str, Any]) -> MessageEnd:
    return assistant({"type": "tool_call", "id": call_id, "name": "show_ui", "arguments": {"messages": list(messages)}})


CREATE = {"version": "v0.9", "createSurface": {"surfaceId": "r", "catalogId": CATALOG_ID}}
ROOT = {"version": "v0.9", "updateComponents": {"surfaceId": "r", "components": [{"id": "root", "component": "Text", "text": "one"}]}}
DATA = {"version": "v0.9", "updateDataModel": {"surfaceId": "r", "path": "/", "value": {"n": 2}}}


def test_a_shown_surface_is_an_a2ui_activity_redrawn_whole_as_it_is_updated() -> None:
    """CopilotKit's A2UI renderer draws an ``a2ui-surface`` activity's operations; a snapshot
    replaces the activity, so it carries every operation the surface has had."""
    j, translator = Journal(), AgUiTranslator(SID)
    ok = {"ok": True, "payload": {"shown": ["r"]}}

    first = translated(j(_show("c1", CREATE, ROOT)), j(ToolEnd("c1", "show_ui", ok)), translator=translator)
    second = translated(j(_show("c2", DATA)), j(ToolEnd("c2", "show_ui", ok)), translator=translator)
    refused = translated(j(_show("c3", DATA)), j(ToolEnd("c3", "show_ui", {"ok": False, "error": "bad"}, is_error=True)), translator=translator)

    activity = [e for e in first if e["type"] == "ACTIVITY_SNAPSHOT"][-1]
    assert (activity["messageId"], activity["activityType"], activity["replace"]) == ("a2ui:r", A2UI_ACTIVITY, True)
    assert activity["content"] == {A2UI_OPERATIONS: [CREATE, ROOT]}
    assert [e for e in second if e["type"] == "ACTIVITY_SNAPSHOT"][-1]["content"] == {A2UI_OPERATIONS: [CREATE, ROOT, DATA]}
    assert "ACTIVITY_SNAPSHOT" not in types(refused), "a refused view is not drawn"


def test_a_deleted_surface_is_drawn_empty() -> None:
    j = Journal()
    gone = {"version": "v0.9", "deleteSurface": {"surfaceId": "r"}}
    ok = {"ok": True, "payload": {}}

    events = translated(j(_show("c1", CREATE, ROOT)), j(ToolEnd("c1", "show_ui", ok)), j(_show("c2", gone)), j(ToolEnd("c2", "show_ui", ok)))

    assert [e["content"] for e in events if e["type"] == "ACTIVITY_SNAPSHOT"][-1] == {A2UI_OPERATIONS: []}


def test_the_task_the_agent_shows_is_the_sessions_state() -> None:
    """Each successful ``update_task`` replaces the state: an AG-UI client (CopilotKit's shared
    state) reads the task from ``STATE_SNAPSHOT``; a refused call changes nothing."""
    j = Journal()
    task = {"goal": "Cheapest paper towels", "steps": [{"title": "Search Shop A", "status": "done"}], "found": []}

    events = translated(
        j(ToolEnd("c1", "update_task", {"ok": True, "payload": {"task": task}})),
        j(ToolEnd("c2", "update_task", {"ok": False, "error": "bad"}, is_error=True)),
    )

    assert [e["snapshot"] for e in events if e["type"] == "STATE_SNAPSHOT"] == [{TASK_STATE: task}]


def test_a_call_only_its_start_announces_is_announced_once() -> None:
    """A stream resumed after the message that made a call still hears of it."""
    j = Journal()

    events = translated(j(ToolStart("c9", "ls", {"path": "."})), j(ToolStart("c9", "ls", {"path": "."})), j(ToolStart("c8", "ls", {}, not_run=True)))

    assert types(events) == ["TOOL_CALL_START", "TOOL_CALL_ARGS", "TOOL_CALL_END"]


def test_the_rest_is_custom_as_our_wire_sends_it() -> None:
    j = Journal()
    approval = ApprovalRequired("a1", "Run rm?", ["approve", "deny"], 300.0)

    asked, newer = translated(j(approval), j(UnknownEvent("FutureThing", {"x": 1})))

    assert (asked["name"], asked["value"]["approval_id"], asked["value"]["options"]) == ("approval_required", "a1", ["approve", "deny"])
    assert (newer["name"], newer["value"]) == ("future_thing", {"x": 1})


def test_subagents_are_the_protocols_own() -> None:
    j = Journal()
    start = SubagentStart(run_id="sub-run", child_session_id="s2", parent_session_id=SID, mode="one_shot", provider="session", label="researcher", call_id="c4")

    started, finished = translated(j(start), j(SubagentEnd(run_id="sub-run", stop_reason="stop", output="found it")))

    assert (started["subagentRunId"], started["name"], started["parentToolCallId"]) == ("sub-run", "researcher", "c4")
    assert (finished["subagentRunId"], finished["result"]) == ("sub-run", "found it")


def test_a_run_a_client_asked_for_keeps_its_id() -> None:
    j = Journal()
    translator = AgUiTranslator(SID, run_ids={RUN: "client-run-7"})

    started, finished = translated(j(RunStart()), j(RunEnd(messages=[])), translator=translator)

    assert started["runId"] == finished["runId"] == "client-run-7"
