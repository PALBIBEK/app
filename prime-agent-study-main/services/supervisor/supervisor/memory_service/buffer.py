"""Write-behind buffer — sync append, durable only on flush."""

from __future__ import annotations

import time
from typing import Any

from supervisor.memory_service.types import SessionEvent


class WriteBehindBuffer:
    """In-memory append path. Token loop must never await I/O here."""

    def __init__(self) -> None:
        self._pending: list[SessionEvent] = []
        self._next_seq: int = 0

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    @property
    def next_seq(self) -> int:
        return self._next_seq

    def seed_next_seq(self, next_seq: int) -> None:
        """After durable load/fork, continue numbering from durable tip + 1."""
        if self._pending:
            raise RuntimeError("cannot seed seq while buffer has pending events")
        self._next_seq = next_seq

    def append(self, type: str, data: Any = None, *, time_ms: int | None = None) -> SessionEvent:
        """Synchronous in-memory append — no I/O."""
        ev = SessionEvent(
            type=type,
            seq=self._next_seq,
            time=time_ms if time_ms is not None else int(time.time() * 1000),
            data={} if data is None else data,
        )
        self._pending.append(ev)
        self._next_seq += 1
        return ev

    def drain(self) -> list[SessionEvent]:
        """Take pending events for flush. Buffer cleared."""
        batch = self._pending
        self._pending = []
        return batch

    def peek(self) -> tuple[SessionEvent, ...]:
        return tuple(self._pending)
