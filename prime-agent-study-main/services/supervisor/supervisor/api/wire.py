"""Map internal Envelope → versioned wire event (snake_case types)."""

from __future__ import annotations

import re
from typing import Any

from supervisor.agent.events import AgentEvent, Envelope, ErrorEvent
from supervisor.api.schemas import WireEvent
from supervisor.journal.serde import encode_event


def _wire_type(payload: AgentEvent) -> str:
    if isinstance(payload, ErrorEvent):
        return "error"
    name = type(payload).__name__
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def envelope_to_wire(env: Envelope) -> WireEvent:
    raw = encode_event(env.payload)
    data: dict[str, Any] = {k: v for k, v in raw.items() if k != "_type"}
    return WireEvent(
        seq=env.seq,
        event_id=env.event_id,
        turn_id=env.turn_id,
        type=_wire_type(env.payload),
        ts_ms=env.ts_ms,
        data=data,
    )
