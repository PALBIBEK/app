"""Thread-safe in-memory session_id → live AgentRuntime mapping.

A restart empties this registry — reconstruct from the journal.
Never persist this map; it's purely in-memory process state.

Features:
- Thread-safe operations with fine-grained locking
- Async context manager for per-session locks
- Metrics tracking for monitoring
"""

from __future__ import annotations

import asyncio
import logging
import threading
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, AsyncIterator

from supervisor.session.core.types import RegistryMetrics

if TYPE_CHECKING:
    pass

__all__ = ["SessionRegistry"]

logger = logging.getLogger(__name__)


class SessionRegistry:
    """In-memory session registry with metrics and async locking.

    Thread-safe mapping from session_id to runtime instances.
    Supports both synchronous access and async context manager
    for exclusive session operations.

    Example:
        registry = SessionRegistry()

        # Simple access
        registry.put("sess-1", runtime)
        rt = registry.get("sess-1")

        # Exclusive access with async lock
        async with registry.lock("sess-1") as rt:
            if rt is not None:
                await rt.do_something()
    """

    def __init__(self) -> None:
        """Initialize empty registry with metrics tracking."""
        self._live: dict[str, Any] = {}
        self._lock = threading.Lock()
        self._async_locks: dict[str, asyncio.Lock] = {}
        self._async_locks_guard = asyncio.Lock()
        self._metrics = RegistryMetrics()

    # =========================================================================
    # Synchronous Operations
    # =========================================================================

    def get(self, session_id: str) -> Any | None:
        """Retrieve runtime by session ID.

        Args:
            session_id: The session identifier

        Returns:
            AgentRuntime if found, None otherwise
        """
        with self._lock:
            return self._live.get(session_id)

    def put(self, session_id: str, runtime: Any) -> None:
        """Store a runtime instance.

        Args:
            session_id: The session identifier
            runtime: AgentRuntime instance to store
        """
        with self._lock:
            is_new = session_id not in self._live
            self._live[session_id] = runtime
            if is_new:
                self._metrics.registrations += 1
                self._metrics.active_sessions += 1
                logger.debug(
                    "registered session",
                    extra={"session_id": session_id, "active": self._metrics.active_sessions},
                )

    def remove(self, session_id: str) -> Any | None:
        """Remove and return a runtime.

        Args:
            session_id: The session identifier

        Returns:
            AgentRuntime if found and removed, None otherwise
        """
        with self._lock:
            runtime = self._live.pop(session_id, None)
            if runtime is not None:
                self._metrics.unregistrations += 1
                self._metrics.active_sessions -= 1
                logger.debug(
                    "unregistered session",
                    extra={"session_id": session_id, "active": self._metrics.active_sessions},
                )
            return runtime

    def all(self) -> list[str]:
        """List all registered session IDs.

        Returns:
            List of session IDs with active runtimes
        """
        with self._lock:
            return list(self._live.keys())

    def __contains__(self, session_id: str) -> bool:
        """Check if session is registered."""
        with self._lock:
            return session_id in self._live

    def __len__(self) -> int:
        """Return number of registered sessions."""
        with self._lock:
            return len(self._live)

    # =========================================================================
    # Async Context Manager for Exclusive Access
    # =========================================================================

    @asynccontextmanager
    async def lock(self, session_id: str) -> AsyncIterator[Any | None]:
        """Acquire per-session lock for exclusive access.

        Provides exclusive access to a session's runtime within the
        context manager scope. Tracks lock contention for metrics.

        Args:
            session_id: The session to lock

        Yields:
            AgentRuntime if found, None otherwise

        Example:
            async with registry.lock("sess-1") as rt:
                if rt is not None:
                    await rt.send_message(...)
        """
        # Get or create per-session lock
        async with self._async_locks_guard:
            if session_id not in self._async_locks:
                self._async_locks[session_id] = asyncio.Lock()
            session_lock = self._async_locks[session_id]

        # Track contention
        self._metrics.lock_acquisitions += 1
        if session_lock.locked():
            self._metrics.lock_contentions += 1
            logger.debug(
                "session lock contention",
                extra={"session_id": session_id},
            )

        async with session_lock:
            yield self.get(session_id)

    async def cleanup_lock(self, session_id: str) -> None:
        """Remove per-session lock after session termination.

        Should be called when a session is fully terminated
        to prevent memory leaks in the lock dictionary.

        Args:
            session_id: The session whose lock should be removed
        """
        async with self._async_locks_guard:
            self._async_locks.pop(session_id, None)

    # =========================================================================
    # Metrics
    # =========================================================================

    def get_metrics(self) -> RegistryMetrics:
        """Return current registry metrics.

        Returns:
            RegistryMetrics with operation counts
        """
        with self._lock:
            # Return a copy to prevent mutation
            return RegistryMetrics(
                registrations=self._metrics.registrations,
                unregistrations=self._metrics.unregistrations,
                lock_acquisitions=self._metrics.lock_acquisitions,
                lock_contentions=self._metrics.lock_contentions,
                active_sessions=self._metrics.active_sessions,
            )

    def reset_metrics(self) -> None:
        """Reset all metrics counters to zero.

        Useful for testing or after metric export.
        """
        with self._lock:
            self._metrics = RegistryMetrics(active_sessions=len(self._live))
