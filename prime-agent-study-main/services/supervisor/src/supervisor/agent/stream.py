"""Event stream with envelope wrapping, sequence numbers, and trace context."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from uuid import uuid4

from supervisor.agent.events import AgentEvent, Envelope, RunEnd, RunStart, TurnEnd, TurnStart
from supervisor.obs.trace_context import format_traceparent, new_trace_id, set_outbound_traceparent

_log = logging.getLogger(__name__)
_SENTINEL = object()
_QUEUE_MAX = 10_000


class EventStream:
    """Wraps AgentEvent payloads into Envelopes with contiguous seq, turn_id, trace_id."""

    __slots__ = ("session_id", "_seq", "_turn_id", "_trace_id", "_seeded_trace_id", "_queue", "_closed")

    def __init__(self, *, session_id: str = "", queue_max: int = _QUEUE_MAX) -> None:
        self.session_id = session_id
        self._seq = 0
        self._turn_id: str | None = None
        self._trace_id: str | None = None
        self._seeded_trace_id: str | None = None
        self._queue: asyncio.Queue[Envelope | object] = asyncio.Queue(maxsize=queue_max)
        self._closed = False

    @property
    def seq(self) -> int:
        return self._seq

    @property
    def trace_id(self) -> str | None:
        return self._trace_id

    def seed_trace_id(self, trace_id: str) -> None:
        """Honor inbound traceparent for the next RunStart."""
        self._seeded_trace_id = trace_id.lower()

    def emit(self, payload: AgentEvent) -> Envelope | None:
        """Wrap and enqueue. Returns None if closed or queue full."""
        if self._closed:
            return None

        # Handle trace/turn lifecycle
        if isinstance(payload, RunStart):
            self._trace_id = self._seeded_trace_id or new_trace_id()
            self._seeded_trace_id = None
            set_outbound_traceparent(format_traceparent(self._trace_id))
        elif isinstance(payload, TurnStart):
            self._turn_id = uuid4().hex

        env = Envelope(
            session_id=self.session_id,
            seq=self._seq,
            event_id=uuid4().hex,
            ts_ms=int(time.time() * 1000),
            turn_id=self._turn_id,
            trace_id=self._trace_id,
            payload=payload,
        )
        self._seq += 1

        # Clear context on lifecycle boundaries
        if isinstance(payload, TurnEnd):
            self._turn_id = None
        elif isinstance(payload, RunEnd):
            self._trace_id = None
            set_outbound_traceparent(None)

        try:
            self._queue.put_nowait(env)
        except asyncio.QueueFull:
            _log.warning("event queue full; dropping seq=%s type=%s", env.seq, type(payload).__name__)
            return None
        return env

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._queue.put_nowait(_SENTINEL)
        except asyncio.QueueFull:
            pass

    async def events(self) -> AsyncIterator[Envelope]:
        while True:
            item = await self._queue.get()
            if item is _SENTINEL:
                break
            yield item  # type: ignore[misc]
