"""
Thread-safe session registry.

Single source of truth for in-memory session state.
Replaces the 5+ dictionaries from the original SessionManager.

Design Principles:
    1. Single source of truth: All session state in one place
    2. Thread-safe: All operations protected by asyncio.Lock
    3. Transactional: Atomic updates via context manager
    4. Observable: Built-in metrics and events
    5. Bounded: Enforces capacity limits

The registry manages:
    - Session entities (Session model)
    - Runtime instances (AgentRuntime)
    - Sandbox handles
    - Seams configurations
    - Active locks (for concurrent access)

Usage:
    registry = SessionRegistry(max_sessions=1000)

    # Register new session
    await registry.register(session, runtime, sandbox)

    # Get session atomically
    async with registry.lock(session_id):
        entry = await registry.get(session_id)
        entry.session = entry.session.with_status(SessionStatus.ACTIVE)
        await registry.update(entry)

    # Iterate safely
    for entry in registry.active_sessions():
        ...
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from supervisor.core.session.errors import (
    SessionAlreadyExistsError,
    SessionNotFoundError,
    SessionQuotaExceededError,
)
from supervisor.core.session.models import Session, SessionMode, SessionStatus

logger = logging.getLogger(__name__)


@dataclass
class SessionEntry:
    """
    Complete session state entry.

    Holds all runtime state for a single session.
    This is the single source of truth (no more 5 dicts).

    Attributes:
        session: Session domain model.
        runtime: Agent runtime instance (if active).
        sandbox: Sandbox handle (if provisioned).
        seams: Seams configuration.
        fanout_task: Event streaming task.
        workspace_dirty: Whether workspace needs sync.
        last_activity: Last activity timestamp.
    """

    session: Session
    runtime: Any = None
    sandbox: Any = None
    seams: Any = None
    fanout_task: asyncio.Task[None] | None = None
    workspace_dirty: bool = False
    last_activity: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def session_id(self) -> str:
        """Get session ID."""
        return self.session.id

    @property
    def tenant_id(self) -> str:
        """Get tenant ID."""
        return self.session.tenant_id

    @property
    def status(self) -> SessionStatus:
        """Get session status."""
        return self.session.status

    @property
    def mode(self) -> SessionMode:
        """Get session mode."""
        return self.session.mode

    @property
    def is_warm(self) -> bool:
        """Check if session is warm (runtime in memory)."""
        return self.runtime is not None

    @property
    def has_sandbox(self) -> bool:
        """Check if session has sandbox."""
        return self.sandbox is not None

    def touch(self) -> None:
        """Update last activity timestamp."""
        self.last_activity = datetime.now(UTC)

    def idle_seconds(self) -> float:
        """Get seconds since last activity."""
        return (datetime.now(UTC) - self.last_activity).total_seconds()


@dataclass
class RegistryMetrics:
    """Registry metrics for monitoring."""

    total_sessions: int = 0
    active_sessions: int = 0
    warm_sessions: int = 0
    sandboxed_sessions: int = 0
    registrations_total: int = 0
    unregistrations_total: int = 0
    lock_acquisitions_total: int = 0
    lock_contentions_total: int = 0


class SessionRegistry:
    """
    Thread-safe in-memory session registry.

    Single source of truth for all session runtime state.
    All operations are protected by asyncio.Lock for thread safety.
    """

    def __init__(
        self,
        *,
        max_sessions: int = 10000,
        max_per_tenant: int = 100,
    ) -> None:
        """
        Initialize registry.

        Args:
            max_sessions: Maximum total sessions.
            max_per_tenant: Maximum sessions per tenant.
        """
        self._max_sessions = max_sessions
        self._max_per_tenant = max_per_tenant

        # Session storage
        self._entries: dict[str, SessionEntry] = {}

        # Tenant index for fast lookups
        self._tenant_index: dict[str, set[str]] = {}

        # Per-session locks for fine-grained concurrency
        self._locks: dict[str, asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()

        # Metrics
        self._metrics = RegistryMetrics()

        logger.info(
            f"SessionRegistry initialized: max_sessions={max_sessions}, "
            f"max_per_tenant={max_per_tenant}"
        )

    @property
    def metrics(self) -> RegistryMetrics:
        """Get registry metrics."""
        return self._metrics

    @property
    def size(self) -> int:
        """Get number of registered sessions."""
        return len(self._entries)

    async def register(
        self,
        session: Session,
        *,
        runtime: Any = None,
        sandbox: Any = None,
        seams: Any = None,
    ) -> SessionEntry:
        """
        Register a new session.

        Args:
            session: Session to register.
            runtime: Optional runtime instance.
            sandbox: Optional sandbox handle.
            seams: Optional seams configuration.

        Returns:
            Created SessionEntry.

        Raises:
            SessionAlreadyExistsError: If session ID exists.
            SessionQuotaExceededError: If limits exceeded.
        """
        async with self._global_lock:
            # Check if already exists
            if session.id in self._entries:
                raise SessionAlreadyExistsError(session_id=session.id)

            # Check global limit
            if len(self._entries) >= self._max_sessions:
                raise SessionQuotaExceededError(
                    message="Maximum sessions reached",
                    session_id=session.id,
                    quota_type="total_sessions",
                    current=len(self._entries),
                    limit=self._max_sessions,
                )

            # Check tenant limit
            tenant_sessions = self._tenant_index.get(session.tenant_id, set())
            if len(tenant_sessions) >= self._max_per_tenant:
                raise SessionQuotaExceededError(
                    message="Maximum sessions per tenant reached",
                    session_id=session.id,
                    quota_type="tenant_sessions",
                    current=len(tenant_sessions),
                    limit=self._max_per_tenant,
                )

            # Create entry
            entry = SessionEntry(
                session=session,
                runtime=runtime,
                sandbox=sandbox,
                seams=seams,
            )

            # Register
            self._entries[session.id] = entry
            self._locks[session.id] = asyncio.Lock()

            # Update tenant index
            if session.tenant_id not in self._tenant_index:
                self._tenant_index[session.tenant_id] = set()
            self._tenant_index[session.tenant_id].add(session.id)

            # Update metrics
            self._metrics.registrations_total += 1
            self._update_metrics()

            logger.debug(f"Registered session: {session.id}")
            return entry

    async def unregister(self, session_id: str) -> SessionEntry | None:
        """
        Unregister a session.

        Args:
            session_id: Session to unregister.

        Returns:
            Removed entry, or None if not found.
        """
        async with self._global_lock:
            entry = self._entries.pop(session_id, None)
            if entry is None:
                return None

            # Update tenant index
            tenant_id = entry.tenant_id
            if tenant_id in self._tenant_index:
                self._tenant_index[tenant_id].discard(session_id)
                if not self._tenant_index[tenant_id]:
                    del self._tenant_index[tenant_id]

            # Remove lock
            self._locks.pop(session_id, None)

            # Update metrics
            self._metrics.unregistrations_total += 1
            self._update_metrics()

            logger.debug(f"Unregistered session: {session_id}")
            return entry

    async def get(self, session_id: str) -> SessionEntry | None:
        """
        Get session entry.

        Args:
            session_id: Session identifier.

        Returns:
            SessionEntry if found, None otherwise.
        """
        return self._entries.get(session_id)

    async def get_or_raise(self, session_id: str) -> SessionEntry:
        """
        Get session entry or raise error.

        Args:
            session_id: Session identifier.

        Returns:
            SessionEntry.

        Raises:
            SessionNotFoundError: If not found.
        """
        entry = self._entries.get(session_id)
        if entry is None:
            raise SessionNotFoundError(session_id=session_id)
        return entry

    async def update(self, entry: SessionEntry) -> None:
        """
        Update session entry.

        Args:
            entry: Updated entry.
        """
        if entry.session_id not in self._entries:
            raise SessionNotFoundError(session_id=entry.session_id)

        self._entries[entry.session_id] = entry
        self._update_metrics()

    async def update_session(self, session: Session) -> None:
        """
        Update session model in entry.

        Args:
            session: Updated session.
        """
        entry = self._entries.get(session.id)
        if entry is None:
            raise SessionNotFoundError(session_id=session.id)

        entry.session = session
        entry.touch()
        self._update_metrics()

    @asynccontextmanager
    async def lock(self, session_id: str) -> AsyncIterator[SessionEntry]:
        """
        Lock session for exclusive access.

        Context manager that acquires per-session lock.

        Args:
            session_id: Session to lock.

        Yields:
            SessionEntry with exclusive access.

        Raises:
            SessionNotFoundError: If session not found.
        """
        # Get or create lock
        async with self._global_lock:
            if session_id not in self._entries:
                raise SessionNotFoundError(session_id=session_id)

            if session_id not in self._locks:
                self._locks[session_id] = asyncio.Lock()

            lock = self._locks[session_id]
            entry = self._entries[session_id]

        # Track contention
        if lock.locked():
            self._metrics.lock_contentions_total += 1

        self._metrics.lock_acquisitions_total += 1

        async with lock:
            # Re-fetch entry in case it changed
            entry = self._entries.get(session_id)
            if entry is None:
                raise SessionNotFoundError(session_id=session_id)

            yield entry

    def contains(self, session_id: str) -> bool:
        """Check if session is registered."""
        return session_id in self._entries

    def tenant_session_count(self, tenant_id: str) -> int:
        """Get session count for tenant."""
        return len(self._tenant_index.get(tenant_id, set()))

    def active_sessions(self) -> Iterator[SessionEntry]:
        """
        Iterate over active sessions.

        Yields:
            SessionEntry for each active session.

        Note:
            This is a snapshot; modifications during iteration
            won't affect the iteration.
        """
        # Snapshot to avoid modification during iteration
        entries = list(self._entries.values())
        for entry in entries:
            if not entry.status.is_terminal:
                yield entry

    def warm_sessions(self) -> Iterator[SessionEntry]:
        """
        Iterate over warm sessions (runtime in memory).

        Yields:
            SessionEntry for each warm session.
        """
        entries = list(self._entries.values())
        for entry in entries:
            if entry.is_warm:
                yield entry

    def idle_sessions(self, idle_threshold_seconds: float) -> Iterator[SessionEntry]:
        """
        Iterate over idle sessions.

        Args:
            idle_threshold_seconds: Minimum idle time.

        Yields:
            SessionEntry for each idle session.
        """
        entries = list(self._entries.values())
        for entry in entries:
            if entry.idle_seconds() > idle_threshold_seconds:
                yield entry

    def sessions_by_tenant(self, tenant_id: str) -> Iterator[SessionEntry]:
        """
        Iterate over sessions for a tenant.

        Args:
            tenant_id: Tenant identifier.

        Yields:
            SessionEntry for each session.
        """
        session_ids = self._tenant_index.get(tenant_id, set())
        for session_id in session_ids:
            entry = self._entries.get(session_id)
            if entry:
                yield entry

    def _update_metrics(self) -> None:
        """Update metrics from current state."""
        self._metrics.total_sessions = len(self._entries)
        self._metrics.active_sessions = sum(
            1 for e in self._entries.values() if not e.status.is_terminal
        )
        self._metrics.warm_sessions = sum(
            1 for e in self._entries.values() if e.is_warm
        )
        self._metrics.sandboxed_sessions = sum(
            1 for e in self._entries.values() if e.has_sandbox
        )

    async def shutdown(self) -> None:
        """
        Shutdown registry.

        Cancels all fanout tasks but does not cleanup runtimes/sandboxes.
        """
        logger.info(f"Shutting down registry with {len(self._entries)} sessions")

        # Cancel all fanout tasks
        for entry in self._entries.values():
            if entry.fanout_task and not entry.fanout_task.done():
                entry.fanout_task.cancel()
                try:
                    await entry.fanout_task
                except asyncio.CancelledError:
                    pass

        logger.info("Registry shutdown complete")

    def get_stats(self) -> dict[str, Any]:
        """Get registry statistics."""
        return {
            "total_sessions": self._metrics.total_sessions,
            "active_sessions": self._metrics.active_sessions,
            "warm_sessions": self._metrics.warm_sessions,
            "sandboxed_sessions": self._metrics.sandboxed_sessions,
            "tenant_count": len(self._tenant_index),
            "registrations_total": self._metrics.registrations_total,
            "unregistrations_total": self._metrics.unregistrations_total,
            "lock_acquisitions_total": self._metrics.lock_acquisitions_total,
            "lock_contentions_total": self._metrics.lock_contentions_total,
            "limits": {
                "max_sessions": self._max_sessions,
                "max_per_tenant": self._max_per_tenant,
            },
        }
