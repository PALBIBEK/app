"""Map internal Envelope → versioned wire event (snake_case types).

The wire is what clients see, not the journal: an event's ``data`` is its journalled
fields less what is the supervisor's own (``_INTERNAL``): the whole conversation a
run's end or a compaction carries (every message was already sent as it happened, and
a long session sent all of them again at every run's end), the text injected into the
model's context (memory, project instructions), and an audience's fingerprint. What a
client may rely on is ``WIRE_VERSION``: it changes when a field a client could read
is removed or changes meaning (version 1 sent every journalled field).
"""

from __future__ import annotations

import re
from typing import Any, Final

from supervisor.agent.events import (
    AgentEvent,
    CompactionEnd,
    Envelope,
    ErrorEvent,
    MemoryContext,
    MemoryInjected,
    MemoryRecall,
    ProjectContext,
    RunEnd,
    SessionNotes,
    UnknownEvent,
)
from supervisor.api.schemas import WIRE_VERSION, WireEvent
from supervisor.journal.serde import encode_event

__all__ = ["WIRE_VERSION", "envelope_to_wire", "wire_data", "wire_type"]

_INTERNAL: Final[dict[type, frozenset[str]]] = {
    RunEnd: frozenset({"messages"}),
    CompactionEnd: frozenset({"messages"}),
    MemoryInjected: frozenset({"text", "shown"}),
    MemoryRecall: frozenset({"text"}),
    ProjectContext: frozenset({"text"}),
    SessionNotes: frozenset({"notes"}),
    MemoryContext: frozenset({"audience"}),
}
"""Fields of an event clients are not sent."""


def wire_type(payload: AgentEvent) -> str:
    """``payload``'s type as clients are sent it: snake case, ``error`` for an ``ErrorEvent``."""
    if isinstance(payload, ErrorEvent):
        return "error"
    name = payload.type_name if isinstance(payload, UnknownEvent) else type(payload).__name__
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def wire_data(payload: AgentEvent) -> dict[str, Any]:
    """``payload``'s fields as clients are sent them."""
    hidden = _INTERNAL.get(type(payload), frozenset())
    data: dict[str, Any] = {k: v for k, v in encode_event(payload).items() if k != "_type" and k not in hidden}
    if isinstance(payload, MemoryInjected):
        data["items"] = len(payload.shown)  # how much was recalled, not what
    return data


def envelope_to_wire(env: Envelope) -> WireEvent:
    return WireEvent(
        v=WIRE_VERSION,
        seq=env.seq,
        event_id=env.event_id,
        turn_id=env.turn_id,
        type=wire_type(env.payload),
        ts_ms=env.ts_ms,
        data=wire_data(env.payload),
    )
