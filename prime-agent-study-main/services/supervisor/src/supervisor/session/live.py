"""Per-session live event fan-out to many SSE subscribers."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Final

from supervisor.agent.events import Envelope

logger = logging.getLogger(__name__)


class Dropped:
    """Put on a slow subscriber's queue once it is disconnected."""


_DROPPED: Final = Dropped()


@dataclass(eq=False)
class LiveSubscription:
    queue: asyncio.Queue[Envelope | Dropped]
    dropped: bool = False

    def __hash__(self) -> int:
        return id(self)


@dataclass
class LiveHub:
    """Multiple concurrent streams per session; slow clients are disconnected."""

    maxsize: int = 256
    _subs: dict[str, set[LiveSubscription]] = field(default_factory=dict)

    def subscribe(self, session_id: str) -> LiveSubscription:
        sub = LiveSubscription(queue=asyncio.Queue(maxsize=self.maxsize))
        self._subs.setdefault(session_id, set()).add(sub)
        return sub

    def unsubscribe(self, session_id: str, sub: LiveSubscription) -> None:
        bucket = self._subs.get(session_id)
        if bucket is None:
            return
        bucket.discard(sub)
        if not bucket:
            self._subs.pop(session_id, None)

    def publish(self, session_id: str, env: Envelope) -> None:
        for sub in list(self._subs.get(session_id, ())):
            try:
                sub.queue.put_nowait(env)
            except asyncio.QueueFull:
                sub.dropped = True
                self.unsubscribe(session_id, sub)
                try:
                    # Wake a blocked consumer so it can exit.
                    sub.queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    sub.queue.put_nowait(_DROPPED)
                except asyncio.QueueFull:
                    logger.warning(
                        "failed to signal slow subscriber session=%s", session_id
                    )

    def drop_session(self, session_id: str) -> None:
        self._subs.pop(session_id, None)

