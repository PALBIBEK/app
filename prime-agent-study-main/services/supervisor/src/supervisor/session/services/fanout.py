"""Per-session live event fan-out to SSE subscribers.

Provides real-time event streaming to multiple concurrent clients
per session with automatic slow client disconnection.

Features:
- Multiple subscribers per session
- Configurable queue size
- Slow client detection and disconnection
- Dropped event signaling
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from supervisor.agent.events import Envelope

__all__ = [
    "LiveHub",
    "LiveSubscription",
    "is_dropped_sentinel",
]

logger = logging.getLogger(__name__)

# Sentinel value for dropped connection signaling
_DROPPED = object()


# =============================================================================
# Subscription Types
# =============================================================================


@dataclass(eq=False)
class LiveSubscription:
    """Per-subscriber event queue for SSE streaming.

    Tracks dropped state for slow client handling.
    Uses object identity for equality to support set membership.
    """

    queue: asyncio.Queue[Envelope | object]
    dropped: bool = False

    def __hash__(self) -> int:
        """Use object identity for hashing."""
        return id(self)


# =============================================================================
# Live Hub
# =============================================================================


@dataclass
class LiveHub:
    """Event fan-out hub for multiple concurrent SSE subscribers.

    Manages subscription lifecycle and handles slow clients
    by disconnecting them and signaling the drop.

    Example:
        hub = LiveHub(maxsize=256)

        # Subscribe
        sub = hub.subscribe("sess-123")

        # In SSE handler
        async for event in sub.queue:
            if is_dropped_sentinel(event):
                break  # Client was too slow
            yield format_sse(event)

        # Cleanup
        hub.unsubscribe("sess-123", sub)

        # Publish from runtime
        hub.publish("sess-123", envelope)
    """

    maxsize: int = 256
    _subs: dict[str, set[LiveSubscription]] = field(default_factory=dict)

    def subscribe(self, session_id: str) -> LiveSubscription:
        """Create a new subscription for a session.

        Args:
            session_id: The session to subscribe to

        Returns:
            LiveSubscription with event queue
        """
        sub = LiveSubscription(queue=asyncio.Queue(maxsize=self.maxsize))
        self._subs.setdefault(session_id, set()).add(sub)
        logger.debug(
            "subscriber added",
            extra={"session_id": session_id, "subscriber_count": len(self._subs[session_id])},
        )
        return sub

    def unsubscribe(self, session_id: str, sub: LiveSubscription) -> None:
        """Remove a subscription.

        Args:
            session_id: The session subscribed to
            sub: The subscription to remove
        """
        bucket = self._subs.get(session_id)
        if bucket is None:
            return
        bucket.discard(sub)
        if not bucket:
            self._subs.pop(session_id, None)
        logger.debug(
            "subscriber removed",
            extra={"session_id": session_id},
        )

    def publish(self, session_id: str, env: Envelope) -> None:
        """Publish an event to all subscribers for a session.

        Slow clients (queue full) are automatically disconnected
        and signaled with a dropped sentinel.

        Args:
            session_id: The session context
            env: The event envelope to publish
        """
        subscribers = self._subs.get(session_id, ())
        for sub in list(subscribers):
            try:
                sub.queue.put_nowait(env)
            except asyncio.QueueFull:
                # Slow client - disconnect and signal
                sub.dropped = True
                self.unsubscribe(session_id, sub)
                self._signal_dropped(session_id, sub)

    def _signal_dropped(self, session_id: str, sub: LiveSubscription) -> None:
        """Signal a slow subscriber that they've been dropped.

        Tries to make room in the queue and insert the sentinel.

        Args:
            session_id: The session context (for logging)
            sub: The subscription being dropped
        """
        try:
            # Wake a blocked consumer so it can exit
            sub.queue.get_nowait()
        except asyncio.QueueEmpty:
            pass

        try:
            sub.queue.put_nowait(_DROPPED)
        except asyncio.QueueFull:
            logger.warning(
                "failed to signal slow subscriber",
                extra={"session_id": session_id},
            )

    def drop_session(self, session_id: str) -> None:
        """Remove all subscribers for a session.

        Called when a session terminates to cleanup resources.

        Args:
            session_id: The session to drop
        """
        count = len(self._subs.pop(session_id, set()))
        if count > 0:
            logger.debug(
                "dropped all subscribers for session",
                extra={"session_id": session_id, "count": count},
            )

    def subscriber_count(self, session_id: str) -> int:
        """Get the number of active subscribers for a session.

        Args:
            session_id: The session to check

        Returns:
            Number of active subscribers
        """
        return len(self._subs.get(session_id, set()))


# =============================================================================
# Helpers
# =============================================================================


def is_dropped_sentinel(item: object) -> bool:
    """Check if an item is the dropped connection sentinel.

    Args:
        item: Item received from subscription queue

    Returns:
        True if this is the dropped sentinel
    """
    return item is _DROPPED
