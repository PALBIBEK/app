"""
Session coordinator.

Thin orchestration layer that composes focused services.
Replaces the god-class SessionManager with clean delegation.

Design Principles:
    1. Thin: Only orchestration, no business logic
    2. Composable: Services injected via constructor
    3. Observable: Centralized logging and tracing
    4. Fail-safe: Graceful degradation

The coordinator:
    - Exposes high-level API (create, send, terminate)
    - Delegates to specialized services
    - Handles cross-cutting concerns (tracing, recovery)
    - Provides unified error handling

Services:
    - Lifecycle: create, terminate, fork
    - Executor: send messages, abort turns
    - Recovery: warm/cold session resume
    - Reaper: idle session cleanup

Usage:
    coordinator = SessionCoordinator(
        lifecycle=lifecycle,
        executor=executor,
        recovery=recovery,
    )

    # Create session
    session = await coordinator.create(...)

    # Send message
    result = await coordinator.send(session_id, message)

    # Graceful shutdown
    await coordinator.shutdown()
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from supervisor.core.session.models import (
    Session,
    SessionConfig,
    SessionMode,
    SessionStatus,
)
from supervisor.core.session.registry import SessionRegistry, SessionEntry
from supervisor.core.session.lifecycle import SessionLifecycle, CreateResult
from supervisor.core.session.executor import TurnExecutor, TurnResult
from supervisor.core.session.protocols import SessionStore, TracingService
from supervisor.core.session.errors import (
    SessionError,
    SessionNotFoundError,
    SessionStateError,
)

logger = logging.getLogger(__name__)


@dataclass
class RecoveryResult:
    """Result of session recovery."""

    session_id: str
    state: str  # warm, cold, recovered, failed
    runtime_restored: bool
    sandbox_restored: bool
    error: str | None = None


class SessionCoordinator:
    """
    Session orchestration layer.

    Composes services for complete session management.
    Provides unified API for all session operations.
    """

    def __init__(
        self,
        *,
        registry: SessionRegistry,
        store: SessionStore,
        lifecycle: SessionLifecycle,
        executor: TurnExecutor,
        tracing: TracingService | None = None,
        draining: bool = False,
    ) -> None:
        """
        Initialize coordinator.

        Args:
            registry: Session registry.
            store: Session store.
            lifecycle: Lifecycle service.
            executor: Turn executor.
            tracing: Tracing service.
            draining: Initial draining state.
        """
        self._registry = registry
        self._store = store
        self._lifecycle = lifecycle
        self._executor = executor
        self._tracing = tracing
        self._draining = draining

        # Background tasks
        self._reaper_task: asyncio.Task[None] | None = None
        self._reaper_interval = 60.0  # Check every minute
        self._idle_timeout = 300.0  # 5 minutes

    @property
    def is_draining(self) -> bool:
        """Check if coordinator is draining."""
        return self._draining

    # ─────────────────────────────────────────────────────────────────────────
    # Session Lifecycle
    # ─────────────────────────────────────────────────────────────────────────

    async def create(
        self,
        tenant_id: str,
        config: SessionConfig,
        *,
        session_id: str | None = None,
        mode: SessionMode | None = None,
        scope_id: str | None = None,
        trace_id: str | None = None,
        seams: Any = None,
    ) -> Session:
        """
        Create a new session.

        Args:
            tenant_id: Owning tenant.
            config: Session configuration.
            session_id: Custom session ID.
            mode: Session mode.
            scope_id: Workspace scope.
            trace_id: Trace ID.
            seams: Seams configuration.

        Returns:
            Created Session.

        Raises:
            SessionError: On creation failure.
        """
        if self._draining:
            raise SessionStateError(
                message="Service is draining, cannot create new sessions",
                current_state="draining",
                attempted_operation="create",
            )

        result = await self._lifecycle.create(
            tenant_id=tenant_id,
            config=config,
            session_id=session_id,
            mode=mode,
            scope_id=scope_id,
            trace_id=trace_id,
            seams=seams,
        )

        return result.session

    async def terminate(
        self,
        session_id: str,
        *,
        reason: str = "user_request",
        force: bool = False,
    ) -> None:
        """
        Terminate a session.

        Args:
            session_id: Session to terminate.
            reason: Termination reason.
            force: Force termination.
        """
        await self._lifecycle.terminate(
            session_id,
            reason=reason,
            force=force,
        )

    async def fork(
        self,
        parent_session_id: str,
        *,
        at_seq: int | None = None,
        new_session_id: str | None = None,
        trace_id: str | None = None,
    ) -> Session:
        """
        Fork a session.

        Args:
            parent_session_id: Parent session.
            at_seq: Sequence to fork at.
            new_session_id: Custom ID for fork.
            trace_id: Trace ID.

        Returns:
            Forked Session.
        """
        result = await self._lifecycle.fork(
            parent_session_id,
            at_seq=at_seq,
            new_session_id=new_session_id,
            trace_id=trace_id,
        )

        return result.session

    # ─────────────────────────────────────────────────────────────────────────
    # Message Handling
    # ─────────────────────────────────────────────────────────────────────────

    async def send(
        self,
        session_id: str,
        message: str,
        *,
        command_id: str | None = None,
        trace_id: str | None = None,
        timeout_seconds: float | None = None,
    ) -> TurnResult:
        """
        Send message to session.

        Ensures session is ready (warms cold sessions).

        Args:
            session_id: Session identifier.
            message: User message.
            command_id: Idempotency key.
            trace_id: Trace ID.
            timeout_seconds: Turn timeout.

        Returns:
            TurnResult with output.

        Raises:
            SessionError: On execution failure.
        """
        # Ensure session is ready
        await self._ensure_ready(session_id, trace_id)

        # Execute turn
        return await self._executor.execute(
            session_id=session_id,
            message=message,
            command_id=command_id,
            trace_id=trace_id,
            timeout_seconds=timeout_seconds,
        )

    async def abort(self, session_id: str) -> bool:
        """
        Abort running turn.

        Args:
            session_id: Session identifier.

        Returns:
            True if aborted, False if no turn running.
        """
        return await self._executor.abort(session_id)

    # ─────────────────────────────────────────────────────────────────────────
    # Session Queries
    # ─────────────────────────────────────────────────────────────────────────

    async def get(self, session_id: str) -> Session | None:
        """
        Get session by ID.

        Checks registry first, then store.

        Args:
            session_id: Session identifier.

        Returns:
            Session if found, None otherwise.
        """
        # Check registry
        entry = await self._registry.get(session_id)
        if entry:
            return entry.session

        # Check store
        return await self._store.get(session_id)

    async def get_or_raise(self, session_id: str) -> Session:
        """
        Get session or raise error.

        Args:
            session_id: Session identifier.

        Returns:
            Session.

        Raises:
            SessionNotFoundError: If not found.
        """
        session = await self.get(session_id)
        if session is None:
            raise SessionNotFoundError(session_id=session_id)
        return session

    async def list(
        self,
        tenant_id: str,
        *,
        status: SessionStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Session]:
        """
        List sessions for tenant.

        Args:
            tenant_id: Tenant identifier.
            status: Filter by status.
            limit: Maximum results.
            offset: Pagination offset.

        Returns:
            List of sessions.
        """
        return await self._store.list_by_tenant(
            tenant_id,
            status=status,
            limit=limit,
            offset=offset,
        )

    async def count(
        self,
        tenant_id: str,
        *,
        status: SessionStatus | None = None,
    ) -> int:
        """
        Count sessions for tenant.

        Args:
            tenant_id: Tenant identifier.
            status: Filter by status.

        Returns:
            Session count.
        """
        return await self._store.count_by_tenant(tenant_id, status=status)

    # ─────────────────────────────────────────────────────────────────────────
    # Session State
    # ─────────────────────────────────────────────────────────────────────────

    def is_warm(self, session_id: str) -> bool:
        """Check if session is warm (in registry)."""
        return self._registry.contains(session_id)

    def is_executing(self, session_id: str) -> bool:
        """Check if session has active turn."""
        return self._executor.is_executing(session_id)

    async def get_entry(self, session_id: str) -> SessionEntry | None:
        """
        Get session entry from registry.

        Args:
            session_id: Session identifier.

        Returns:
            SessionEntry if warm, None otherwise.
        """
        return await self._registry.get(session_id)

    # ─────────────────────────────────────────────────────────────────────────
    # Recovery
    # ─────────────────────────────────────────────────────────────────────────

    async def _ensure_ready(
        self,
        session_id: str,
        trace_id: str | None = None,
    ) -> SessionEntry:
        """
        Ensure session is ready to receive messages.

        Warms cold sessions if needed.

        Args:
            session_id: Session identifier.
            trace_id: Trace ID.

        Returns:
            Ready SessionEntry.

        Raises:
            SessionNotFoundError: If session not found.
            SessionTerminatedError: If session terminated.
            SessionRecoveryError: If recovery fails.
        """
        # Check if already warm
        entry = await self._registry.get(session_id)
        if entry is not None:
            if entry.session.status.is_terminal:
                from supervisor.core.session.errors import SessionTerminatedError
                raise SessionTerminatedError(session_id=session_id)
            return entry

        # Load from store
        session = await self._store.get(session_id)
        if session is None:
            raise SessionNotFoundError(session_id=session_id)

        if session.status.is_terminal:
            from supervisor.core.session.errors import SessionTerminatedError
            raise SessionTerminatedError(session_id=session_id)

        # Recover session
        logger.info(f"Recovering cold session: {session_id}")

        result = await self._lifecycle.create(
            tenant_id=session.tenant_id,
            config=session.config,
            session_id=session.id,
            mode=session.mode,
            scope_id=session.scope_id,
            trace_id=trace_id,
        )

        entry = await self._registry.get(session_id)
        if entry is None:
            raise SessionStateError(
                message="Recovery failed - session not in registry",
                session_id=session_id,
                current_state="recovery_failed",
                attempted_operation="ensure_ready",
            )

        return entry

    # ─────────────────────────────────────────────────────────────────────────
    # Lifecycle Management
    # ─────────────────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """
        Start coordinator.

        Starts background tasks.
        """
        logger.info("Starting session coordinator")

        # Start idle reaper
        self._reaper_task = asyncio.create_task(
            self._idle_reaper_loop(),
            name="session-reaper",
        )

    async def shutdown(self, timeout: float = 30.0) -> None:
        """
        Shutdown coordinator.

        Terminates all sessions gracefully.

        Args:
            timeout: Maximum shutdown time.
        """
        logger.info("Shutting down session coordinator")

        self._draining = True

        # Stop reaper
        if self._reaper_task and not self._reaper_task.done():
            self._reaper_task.cancel()
            try:
                await self._reaper_task
            except asyncio.CancelledError:
                pass

        # Terminate all active sessions
        deadline = asyncio.get_event_loop().time() + timeout

        for entry in list(self._registry.active_sessions()):
            if asyncio.get_event_loop().time() >= deadline:
                logger.warning("Shutdown timeout reached")
                break

            try:
                await self._lifecycle.terminate(
                    entry.session_id,
                    reason="shutdown",
                    force=True,
                )
            except Exception as e:
                logger.warning(f"Failed to terminate session: {e}")

        # Shutdown registry
        await self._registry.shutdown()

        logger.info("Session coordinator shutdown complete")

    async def drain(self, timeout: float = 60.0) -> None:
        """
        Drain coordinator for deployment.

        Stops accepting new sessions and waits for in-flight.

        Args:
            timeout: Maximum drain time.
        """
        logger.info("Draining session coordinator")

        self._draining = True

        # Wait for active turns to complete
        deadline = asyncio.get_event_loop().time() + timeout

        while self._executor.get_active_turns():
            if asyncio.get_event_loop().time() >= deadline:
                logger.warning("Drain timeout reached")
                break
            await asyncio.sleep(1)

        logger.info("Coordinator drained")

    # ─────────────────────────────────────────────────────────────────────────
    # Background Tasks
    # ─────────────────────────────────────────────────────────────────────────

    async def _idle_reaper_loop(self) -> None:
        """Background task to reap idle sessions."""
        while not self._draining:
            try:
                await asyncio.sleep(self._reaper_interval)
                await self._reap_idle_sessions()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Reaper error: {e}")

    async def _reap_idle_sessions(self) -> int:
        """Reap idle sessions."""
        reaped = 0

        for entry in list(self._registry.idle_sessions(self._idle_timeout)):
            # Skip if busy
            if entry.status.is_busy:
                continue

            # Skip if stateless mode (already ephemeral)
            if entry.mode == SessionMode.STATELESS:
                continue

            try:
                logger.debug(f"Reaping idle session: {entry.session_id}")
                await self._lifecycle.terminate(
                    entry.session_id,
                    reason="idle_timeout",
                )
                reaped += 1
            except Exception as e:
                logger.warning(f"Failed to reap session: {e}")

        if reaped > 0:
            logger.info(f"Reaped {reaped} idle sessions")

        return reaped

    # ─────────────────────────────────────────────────────────────────────────
    # Statistics
    # ─────────────────────────────────────────────────────────────────────────

    def get_stats(self) -> dict[str, Any]:
        """Get coordinator statistics."""
        return {
            "draining": self._draining,
            "registry": self._registry.get_stats(),
            "active_turns": len(self._executor.get_active_turns()),
        }
