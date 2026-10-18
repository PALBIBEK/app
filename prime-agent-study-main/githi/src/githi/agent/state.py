"""Mutable run state, plus the listener registry."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Callable

from ..llm.usage import UsageTracker
from ..types import AssistantMessage, Message, StopReason, Usage
from .events import Event


@dataclass
class AgentState:
    messages: list[Message] = field(default_factory=list)
    turns: int = 0
    tool_calls: int = 0
    tool_counts: Counter = field(default_factory=Counter)
    """Per-tool call counts. Kept alongside `turns` because the gap between
    them is the evidence that the model is batching -- several tool calls in
    one assistant message is what keeps turn count (and therefore cost) down."""
    usage: UsageTracker = field(default_factory=UsageTracker)
    stop_reason: StopReason | None = None
    _listeners: list[Callable[[Event], None]] = field(default_factory=list)

    def append(self, message: Message) -> None:
        self.messages.append(message)

    def record_turn(self, usage: Usage) -> None:
        self.turns += 1
        self.usage.add(usage)

    def record_tool_call(self, name: str) -> None:
        self.tool_calls += 1
        self.tool_counts[name] += 1

    def last_assistant_text(self) -> str:
        """The most recent assistant message that actually said something.

        Messages that only request tools are skipped: they are steps in the
        work, not the answer, and reporting one as the result yields an empty
        string exactly when the run did the most.
        """
        for message in reversed(self.messages):
            if isinstance(message, AssistantMessage) and message.content.strip():
                return message.content
        return ""

    # --- listeners ---

    def on(self, listener: Callable[[Event], None]) -> None:
        self._listeners.append(listener)

    def off(self, listener: Callable[[Event], None]) -> None:
        """Remove a listener if present. Silent when absent -- an
        unsubscribe called twice is harmless, not an error."""
        if listener in self._listeners:
            self._listeners.remove(listener)

    def emit(self, event: Event) -> None:
        for listener in self._listeners:
            try:
                listener(event)
            except Exception:  # noqa: BLE001 - observability must not fail a run
                continue
