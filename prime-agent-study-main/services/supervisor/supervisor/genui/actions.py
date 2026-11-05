"""A person's action on a surface the agent drew, as the agent's next message.

A ``Button`` declares an event (``"action": {"event": {"name": ..., "context": {...}}}``);
clicked, the client sends A2UI's own client-to-server ``action`` (its schema vendored
beside the others), carried over AG-UI as ``forwardedProps.a2uiAction.userAction``, the
convention of AG-UI's A2UI middleware (``supervisor.api.agui``).

An action is only taken from the surface as the session last drew it: it names that
surface, a button on it, and the event that button declares. The session is told it as a
user message (``action_text``), so every runtime and model reads it the same way. Each
drawing of a surface takes one answer: the API gives the message the drawing's command id
(its surface and ``Drawing.seq``), so the same click sent again is the same message, and
another answer to a drawing already answered is refused until the agent draws it again.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final

from supervisor.agent.events import Envelope, ToolEnd, ToolStart
from supervisor.agent.runtimes.githi.render import tool_result_failed
from supervisor.genui import SHOW_UI, surface_of, validator

__all__ = [
    "ACTION_PREFIX",
    "JOURNAL_TYPES",
    "MAX_CONTEXT_CHARS",
    "Drawing",
    "action_problem",
    "action_text",
    "drawings",
]

ACTION_PREFIX: Final = "UI action: "
"""How the message a person's action becomes begins; the rest is the action as JSON. The
chat recognises it by this prefix (``frontend/src/lib/session/reports.ts``)."""
MAX_CONTEXT_CHARS: Final = 4000
"""The most an action's context may say (the values of the inputs it sends)."""
_ACTION: Final = "https://a2ui.org/specification/v0_9/client_to_server.json#/properties/action"
JOURNAL_TYPES: Final = (ToolStart.__name__, ToolEnd.__name__)
"""The journal events a surface's drawings are read from."""


@dataclass
class Drawing:
    """A surface as the session last drew it."""

    surface_id: str
    seq: int
    """The journal seq of the call that last drew it: which drawing an answer is to."""
    components: dict[str, dict[str, Any]] = field(default_factory=dict)


def drawings(envelopes: Iterable[Envelope]) -> dict[str, Drawing]:
    """The surfaces a session's successful ``show_ui`` calls left, from its ``ToolStart`` and
    ``ToolEnd`` events in order: each with its components (later ones replacing earlier ones
    of the same id) and the seq of the call that drew it last."""
    asked: dict[str, list[Mapping[str, Any]]] = {}
    surfaces: dict[str, Drawing] = {}
    for env in envelopes:
        p = env.payload
        if isinstance(p, ToolStart) and p.tool_name == SHOW_UI and isinstance(p.args.get("messages"), list):
            asked[p.tool_call_id] = [m for m in p.args["messages"] if isinstance(m, Mapping)]
        elif isinstance(p, ToolEnd) and p.tool_name == SHOW_UI:
            messages = asked.pop(p.tool_call_id, [])
            if not (p.is_error or tool_result_failed(p.result)):
                _draw(surfaces, messages, env.seq)
    return surfaces


def _draw(surfaces: dict[str, Drawing], messages: list[Mapping[str, Any]], seq: int) -> None:
    for message in messages:
        surface = surface_of(message)
        if surface is None:
            continue
        if "deleteSurface" in message:
            surfaces.pop(surface, None)
            continue
        drawing = surfaces.setdefault(surface, Drawing(surface, seq))
        drawing.seq = seq
        if "createSurface" in message:
            drawing.components.clear()
        for component in message.get("updateComponents", {}).get("components", []):
            if isinstance(component, Mapping) and isinstance(component.get("id"), str):
                drawing.components[component["id"]] = dict(component)


def action_problem(action: Any, surfaces: Mapping[str, Drawing]) -> str | None:
    """Why ``action`` cannot be the person's answer, or None: it must be A2UI's action, on a
    surface the session drew, from a button on it that declares that event."""
    if (error := next(iter(validator(_ACTION).iter_errors(action)), None)) is not None:
        where = "/".join(str(p) for p in error.absolute_path)
        return f"not an A2UI v0.9 action: {f'{where}: ' if where else ''}{error.message}"[:300]
    drawing = surfaces.get(action["surfaceId"])
    if drawing is None:
        return f"surface {action['surfaceId']!r} is not one this session shows"
    source = drawing.components.get(action["sourceComponentId"])
    event = (source or {}).get("action", {}).get("event", {})
    if source is None or source.get("component") != "Button":
        return f"surface {drawing.surface_id!r} has no button {action['sourceComponentId']!r}"
    if event.get("name") != action["name"]:
        return f"button {action['sourceComponentId']!r} sends {event.get('name')!r}, not {action['name']!r}"
    if len(json.dumps(action["context"], ensure_ascii=False)) > MAX_CONTEXT_CHARS:
        return f"the action's context is over {MAX_CONTEXT_CHARS} characters"
    return None


def action_text(action: Mapping[str, Any]) -> str:
    """The message the session is told: ``ACTION_PREFIX`` and the action as JSON, without its
    timestamp (the same click sent again is the same message)."""
    said = {k: action[k] for k in ("surfaceId", "sourceComponentId", "name", "context")}
    return ACTION_PREFIX + json.dumps(said, ensure_ascii=False, sort_keys=True)

