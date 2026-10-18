"""Observability events.

Not streaming -- these are discrete lifecycle notifications a caller can
subscribe to for logging or progress. Token streaming was deliberately left
out; this is what remains, and it is enough to see what a run is doing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class EventType(str, Enum):
    TURN_START = "turn_start"
    TURN_END = "turn_end"
    TOOL_START = "tool_start"
    TOOL_END = "tool_end"
    TOOL_PROGRESS = "tool_progress"
    AGENT_END = "agent_end"
    MEMORY_RECALL = "memory_recall"
    """Memory injected context for this prompt. Emitted so the injection is
    visible: silent recall is what made the earlier 0%-improvement bug
    undiagnosable -- the only way to tell whether memory did anything was to
    read the wire payload."""
    TEXT_DELTA = "text_delta"
    """A fragment of assistant text as it arrives. Carries the new text only,
    never the running total -- a renderer appends it, and handing over the
    total would make it reprint everything on every chunk."""
    THINKING_START = "thinking_start"
    THINKING_DELTA = "thinking_delta"
    """A fragment of extended reasoning (pi's thinking_delta). Separate from
    TEXT_DELTA so the UI can style / collapse it without mistaking it for the
    answer."""
    THINKING_END = "thinking_end"


@dataclass
class Event:
    type: EventType
    data: dict[str, Any] = field(default_factory=dict)
