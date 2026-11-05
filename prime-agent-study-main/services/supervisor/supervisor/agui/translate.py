"""Our journal, event by event, as AG-UI events.

| Journal | AG-UI |
|---|---|
| ``RunStart`` / ``RunEnd`` | ``RUN_STARTED`` / ``RUN_FINISHED`` (``cancelled`` for a stop), or ``RUN_ERROR`` |
| the reply being written (``Partial``) | ``TEXT_MESSAGE_START`` and ``TEXT_MESSAGE_CONTENT`` as it grows |
| ``MessageEnd`` | its reasoning (``REASONING_*``), text (``TEXT_MESSAGE_*``, ending the one streamed) and tool calls (``TOOL_CALL_START/ARGS/END``) |
| ``ToolEnd`` | ``TOOL_CALL_RESULT``: the text the model read, and its images |
| a ``show_ui`` call that succeeded | ``ACTIVITY_SNAPSHOT`` of each A2UI surface it drew, as CopilotKit's A2UI renderer reads it |
| ``SubagentStart`` / ``SubagentEnd`` | ``SUBAGENT_STARTED`` / ``SUBAGENT_FINISHED`` |
| everything else (approvals, sandboxes, notices, errors) | ``CUSTOM``, named and shaped as our wire sends it |

A translator follows one stream: it remembers what a later event completes (how a run ended,
the reply streamed so far, each surface's operations). A stream resumed mid-run starts without
that memory, as AG-UI's own resumption does: a surface is then drawn from the operations seen
since.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from typing import Any, Final

from ag_ui.core import (
    ActivitySnapshotEvent,
    BaseEvent,
    CustomEvent,
    DataSource,
    EventType,
    ImagePart,
    ReasoningEndEvent,
    ReasoningMessageContentEvent,
    ReasoningMessageEndEvent,
    ReasoningMessageStartEvent,
    ReasoningStartEvent,
    RunErrorEvent,
    RunFinishedCancelledOutcome,
    RunFinishedEvent,
    RunStartedEvent,
    SubagentFinishedEvent,
    SubagentStartedEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    TextPart,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
)

from supervisor.agent.events import (
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
)
from supervisor.agent.runtimes.githi.render import (
    tool_result_failed,
    tool_result_images,
    tool_result_text,
)
from supervisor.api.wire import wire_data, wire_type
from supervisor.genui import SHOW_UI, surface_of
from supervisor.journal.partials import Partial

__all__ = ["A2UI_ACTIVITY", "A2UI_OPERATIONS", "AgUiTranslator"]

A2UI_ACTIVITY: Final = "a2ui-surface"
"""The activity type CopilotKit's A2UI renderer draws (``@copilotkit/react-core``)."""
A2UI_OPERATIONS: Final = "a2ui_operations"
"""The key of an A2UI activity's content that holds the surface's A2UI messages."""
_FAILED_STOPS: Final = frozenset({"error", "timeout"})
_CANCELLED_STOPS: Final = frozenset({"aborted", "cancelled"})

type Events = list[BaseEvent]


class AgUiTranslator:
    """One stream's journal events, and partial replies, as AG-UI events.

    ``thread_id`` is the session; a run's id is its ``turn_id``, unless ``run_ids`` names
    another (the id an AG-UI client gave the run it asked for).
    """

    def __init__(self, thread_id: str, *, run_ids: Mapping[str, str] | None = None) -> None:
        self._thread = thread_id
        self._run_ids = dict(run_ids or {})
        self._stops: dict[str, str] = {}  # run -> how its turn ended (TurnEnd)
        self._errors: dict[str, ErrorEvent] = {}  # run -> its last error
        self._streaming: dict[str, tuple[str, str]] = {}  # run -> (message id, text sent so far)
        self._calls: set[str] = set()  # tool calls already announced
        self._ui_calls: dict[str, list[dict[str, Any]]] = {}  # show_ui call -> its A2UI messages
        self._surfaces: dict[str, list[dict[str, Any]]] = {}  # surface -> its A2UI messages so far
        self._handlers: dict[type, Callable[[Envelope, Any], Events]] = {
            RunStart: self._run_start,
            RunEnd: self._run_end,
            TurnStart: lambda _env, _p: [],
            TurnEnd: self._turn_end,
            ErrorEvent: self._error,
            MessageEnd: self._message,
            ToolStart: self._tool_start,
            ToolEnd: self._tool_end,
            SubagentStart: self._subagent_start,
            SubagentEnd: self._subagent_end,
        }

    def run_id(self, turn_id: str | None) -> str:
        """The AG-UI run id of one of our runs."""
        return self._run_ids.get(turn_id or "", turn_id or "")

    def envelope(self, env: Envelope) -> Events:
        """``env`` as the AG-UI events it is."""
        handler = self._handlers.get(type(env.payload), self._custom)
        events = handler(env, env.payload)
        for event in events:
            event.timestamp = env.ts_ms
        return events

    def partial(self, partial: Partial) -> Events:
        """The reply being written, as the text it added since the last one."""
        message_id = f"{partial.run_id}:{partial.step}"
        events: Events = []
        open_id, sent = self._streaming.get(partial.run_id, ("", ""))
        if open_id and open_id != message_id:  # a new step: the last one's reply is over
            events.append(TextMessageEndEvent(type=EventType.TEXT_MESSAGE_END, message_id=open_id))
            open_id, sent = "", ""
        if not open_id:
            events.append(TextMessageStartEvent(type=EventType.TEXT_MESSAGE_START, message_id=message_id, role="assistant"))
        if partial.text.startswith(sent) and len(partial.text) > len(sent):
            events.append(_text(message_id, partial.text[len(sent):]))
            sent = partial.text
        self._streaming[partial.run_id] = (message_id, sent)
        return events

    # Runs ----------------------------------------------------------------------------

    def _run_start(self, env: Envelope, _p: RunStart) -> Events:
        return [RunStartedEvent(type=EventType.RUN_STARTED, thread_id=self._thread, run_id=self.run_id(env.turn_id))]

    def _turn_end(self, env: Envelope, p: TurnEnd) -> Events:
        self._stops[env.turn_id or ""] = p.stop_reason
        return []

    def _error(self, env: Envelope, p: ErrorEvent) -> Events:
        self._errors[env.turn_id or ""] = p
        return self._custom(env, p)  # said as it happens: a retried error does not end the run

    def _run_end(self, env: Envelope, _p: RunEnd) -> Events:
        run = env.turn_id or ""
        stop, error = self._stops.pop(run, None), self._errors.pop(run, None)
        events = self._end_stream(run)
        if stop in _FAILED_STOPS:
            message = error.detail if error is not None else f"the run ended: {stop}"
            code = error.error_class if error is not None else stop
            return [*events, RunErrorEvent(type=EventType.RUN_ERROR, message=message, code=code)]
        outcome = RunFinishedCancelledOutcome() if stop in _CANCELLED_STOPS else None
        return [*events, RunFinishedEvent(type=EventType.RUN_FINISHED, thread_id=self._thread, run_id=self.run_id(run), outcome=outcome)]

    def _end_stream(self, run: str) -> Events:
        open_id, _ = self._streaming.pop(run, ("", ""))
        return [TextMessageEndEvent(type=EventType.TEXT_MESSAGE_END, message_id=open_id)] if open_id else []

    # Messages ------------------------------------------------------------------------

    def _message(self, env: Envelope, p: MessageEnd) -> Events:
        if p.role != "assistant":
            text = _joined(p.content, "text")
            return _whole_text(env.event_id, "user", text) if p.role == "user" and text else []
        events = self._reasoning(f"{env.event_id}:reasoning", p.content)
        events += self._reply(env, _joined(p.content, "text"))
        for part in p.content:
            if part.get("type") == "tool_call":
                events += self._announce(str(part.get("id") or ""), str(part.get("name") or ""), part.get("arguments"), env.event_id)
        return events

    def _reply(self, env: Envelope, text: str) -> Events:
        """The reply's text: the end of the one streamed while it was written, or all of it."""
        open_id, sent = self._streaming.pop(env.turn_id or "", ("", ""))
        if not open_id:
            return _whole_text(env.event_id, "assistant", text) if text else []
        if not text.startswith(sent):  # the written reply is not what streamed: close it, say it whole
            return [TextMessageEndEvent(type=EventType.TEXT_MESSAGE_END, message_id=open_id), *_whole_text(env.event_id, "assistant", text)]
        rest = [_text(open_id, text[len(sent):])] if len(text) > len(sent) else []
        return [*rest, TextMessageEndEvent(type=EventType.TEXT_MESSAGE_END, message_id=open_id)]

    @staticmethod
    def _reasoning(message_id: str, parts: Iterable[Mapping[str, Any]]) -> Events:
        """The reasoning a person may read: not redacted, not empty (signed or encrypted
        reasoning is replayed to its model, never shown)."""
        text = "\n\n".join(
            str(p.get("text") or "") for p in parts if p.get("type") == "reasoning" and not p.get("redacted") and str(p.get("text") or "").strip()
        )
        if not text:
            return []
        return [
            ReasoningStartEvent(type=EventType.REASONING_START, message_id=message_id),
            ReasoningMessageStartEvent(type=EventType.REASONING_MESSAGE_START, message_id=message_id, role="reasoning"),
            ReasoningMessageContentEvent(type=EventType.REASONING_MESSAGE_CONTENT, message_id=message_id, delta=text),
            ReasoningMessageEndEvent(type=EventType.REASONING_MESSAGE_END, message_id=message_id),
            ReasoningEndEvent(type=EventType.REASONING_END, message_id=message_id),
        ]

    # Tools ---------------------------------------------------------------------------

    def _announce(self, call_id: str, name: str, arguments: Any, parent: str | None) -> Events:
        """A tool call the model made, once: its name, then its arguments as JSON."""
        if not call_id or call_id in self._calls:
            return []
        self._calls.add(call_id)
        if name == SHOW_UI and isinstance(arguments, Mapping) and isinstance(arguments.get("messages"), list):
            self._ui_calls[call_id] = [dict(m) for m in arguments["messages"] if isinstance(m, Mapping)]
        args = arguments if isinstance(arguments, str) else json.dumps(arguments if arguments is not None else {}, ensure_ascii=False)
        return [
            ToolCallStartEvent(type=EventType.TOOL_CALL_START, tool_call_id=call_id, tool_call_name=name, parent_message_id=parent),
            ToolCallArgsEvent(type=EventType.TOOL_CALL_ARGS, tool_call_id=call_id, delta=args),
            ToolCallEndEvent(type=EventType.TOOL_CALL_END, tool_call_id=call_id),
        ]

    def _tool_start(self, _env: Envelope, p: ToolStart) -> Events:
        # Normally announced with the message that made it; a call the journal has no such
        # message for (resumed after it, a runtime that writes none) is announced here.
        return [] if p.not_run else self._announce(p.tool_call_id, p.tool_name, p.args, None)

    def _tool_end(self, env: Envelope, p: ToolEnd) -> Events:
        text = tool_result_text(p.result, p.tool_name)
        images = tool_result_images(p.result)
        content: str | list[Any] = text
        if images:
            content = [TextPart(text=text), *(ImagePart(source=DataSource(value=i["data"], mime_type=i["mime_type"])) for i in images)]
        result = ToolCallResultEvent(
            type=EventType.TOOL_CALL_RESULT, message_id=env.event_id, tool_call_id=p.tool_call_id, content=content, role="tool"
        )
        drawn = self._ui_calls.pop(p.tool_call_id, [])
        ok = not (p.is_error or tool_result_failed(p.result))
        return [result, *(self._draw(drawn) if ok else [])]

    def _draw(self, messages: list[dict[str, Any]]) -> Events:
        """Each surface ``messages`` touched, as all its operations so far: an activity is a
        snapshot, so a later call that updates a surface redraws it whole."""
        touched: list[str] = []
        for message in messages:
            surface = surface_of(message)
            if surface is None:
                continue
            if "deleteSurface" in message:
                self._surfaces.pop(surface, None)
            else:
                self._surfaces.setdefault(surface, []).append(message)
            if surface not in touched:
                touched.append(surface)
        return [
            ActivitySnapshotEvent(
                type=EventType.ACTIVITY_SNAPSHOT,
                message_id=f"a2ui:{surface}",
                activity_type=A2UI_ACTIVITY,
                content={A2UI_OPERATIONS: list(self._surfaces.get(surface, []))},
                replace=True,
            )
            for surface in touched
        ]

    # Subagents and the rest ------------------------------------------------------------

    @staticmethod
    def _subagent_start(_env: Envelope, p: SubagentStart) -> Events:
        return [
            SubagentStartedEvent(
                type=EventType.SUBAGENT_STARTED, subagent_run_id=p.run_id, name=p.label or p.mode, parent_tool_call_id=p.call_id
            )
        ]

    @staticmethod
    def _subagent_end(_env: Envelope, p: SubagentEnd) -> Events:
        return [SubagentFinishedEvent(type=EventType.SUBAGENT_FINISHED, subagent_run_id=p.run_id, result=p.output)]

    @staticmethod
    def _custom(_env: Envelope, payload: Any) -> Events:
        return [CustomEvent(type=EventType.CUSTOM, name=wire_type(payload), value=wire_data(payload))]


def _joined(parts: Iterable[Mapping[str, Any]], kind: str) -> str:
    return "".join(str(p.get("text") or "") for p in parts if p.get("type") == kind)


def _text(message_id: str, delta: str) -> TextMessageContentEvent:
    return TextMessageContentEvent(type=EventType.TEXT_MESSAGE_CONTENT, message_id=message_id, delta=delta)


def _whole_text(message_id: str, role: str, text: str) -> Events:
    return [
        TextMessageStartEvent(type=EventType.TEXT_MESSAGE_START, message_id=message_id, role=role),  # type: ignore[arg-type]
        _text(message_id, text),
        TextMessageEndEvent(type=EventType.TEXT_MESSAGE_END, message_id=message_id),
    ]
