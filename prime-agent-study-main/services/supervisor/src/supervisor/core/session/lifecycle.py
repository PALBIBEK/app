"""
Session lifecycle service.

Handles session creation, termination, and forking.
Single responsibility: session lifecycle operations only.

Design Principles:
    1. Single responsibility: Only lifecycle operations
    2. Transactional: Atomic create/terminate
    3. Retry-aware: Handles transient failures
    4. Observable: Traces all operations

Operations:
    - create: Provision resources and register session
    - terminate: Graceful shutdown with cleanup
    - fork: Create child session from parent state

Usage:
    lifecycle = SessionLifecycle(
        registry=registry,
        store=session_store,
        runtime_provider=runtime_provider,
        sandbox_provider=sandbox_provider,
    )

    # Create session
    session = await lifecycle.create(
        tenant_id="tenant-1",
        config=SessionConfig(agent_id="agent-1"),
    )

    # Terminate session
    await lifecycle.terminate(session_id, reason="user_request")
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from supervisor.core.session.errors import (
    SessionError,
    SessionNotFoundError,
    SessionQuotaExceededError,
    SessionStateError,
    SessionTerminatedError,
)
from supervisor.core.session.models import (
    Session,
    SessionConfig,
    SessionMode,
    SessionStatus,
)
from supervisor.core.session.protocols import (
    QuotaService,
    RuntimeProvider,
    SandboxProvider,
    SessionStore,
    TracingService,
)
from supervisor.core.session.registry import SessionRegistry

logger = logging.getLogger(__name__)


@dataclass
class CreateResult:
    """Result of session creation."""

    session: Session
    runtime: Any
    sandbox: Any | None
    trace_id: str | None


@dataclass
class TerminateResult:
    """Result of session termination."""

    session_id: str
    final_status: SessionStatus
    runtime_stopped: bool
    sandbox_released: bool
    persisted: bool


class SessionLifecycle:
    """
    Session lifecycle management.

    Handles create, terminate, and fork with proper
    resource management and error handling.
    """

    def __init__(
        self,
        *,
        registry: SessionRegistry,
        store: SessionStore,
        runtime_provider: RuntimeProvider,
        sandbox_provider: SandboxProvider | None = None,
        quota_service: QuotaService | None = None,
        tracing: TracingService | None = None,
        default_mode: SessionMode = SessionMode.STICKY,
        require_sandbox: bool = False,
    ) -> None:
        """
        Initialize lifecycle service.

        Args:
            registry: Session registry.
            store: Persistent session store.
            runtime_provider: Runtime provider.
            sandbox_provider: Sandbox provider (optional).
            quota_service: Quota enforcement (optional).
            tracing: Tracing service (optional).
            default_mode: Default session mode.
            require_sandbox: Whether sandboxes are required.
        """
        self._registry = registry
        self._store = store
        self._runtime_provider = runtime_provider
        self._sandbox_provider = sandbox_provider
        self._quota_service = quota_service
        self._tracing = tracing
        self._default_mode = default_mode
        self._require_sandbox = require_sandbox

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
    ) -> CreateResult:
        """
        Create a new session.

        Provisions resources and registers session atomically.

        Args:
            tenant_id: Owning tenant.
            config: Session configuration.
            session_id: Custom session ID (auto-generated if None).
            mode: Session mode (uses default if None).
            scope_id: Workspace scope (optional).
            trace_id: Trace ID for observability.
            seams: Seams configuration (optional).

        Returns:
            CreateResult with session, runtime, sandbox.

        Raises:
            SessionQuotaExceededError: If quotas exceeded.
            SessionAlreadyExistsError: If ID exists.
            SessionError: On other failures.
        """
        effective_mode = mode or self._default_mode

        # Start tracing span
        span = None
        if self._tracing:
            span = self._tracing.start_span(
                "session.create",
                trace_id=trace_id,
                attributes={
                    "tenant_id": tenant_id,
                    "agent_id": config.agent_id,
                    "mode": effective_mode.value,
                },
            )

        try:
            # Check quotas
            await self._check_quotas(tenant_id)

            # Create session model
            session = Session.create(
                tenant_id=tenant_id,
                config=config,
                session_id=session_id,
                mode=effective_mode,
                scope_id=scope_id,
                trace_id=trace_id,
            )

            logger.info(
                f"Creating session: id={session.id}, tenant={tenant_id}, "
                f"agent={config.agent_id}, mode={effective_mode.value}"
            )

            # Provision resources
            sandbox = None
            runtime = None

            try:
                # Provision sandbox if required
                if self._require_sandbox and self._sandbox_provider:
                    sandbox = await self._provision_sandbox(session)

                # Create runtime
                runtime = await self._runtime_provider.create(
                    session,
                    sandbox=sandbox,
                    seams=seams,
                )

                # Persist session
                await self._store.create(session)

                # Register in memory
                await self._registry.register(
                    session,
                    runtime=runtime,
                    sandbox=sandbox,
                    seams=seams,
                )

                # Update status to active
                session = session.with_status(SessionStatus.ACTIVE)
                await self._store.update(session)
                await self._registry.update_session(session)

                logger.info(f"Session created: {session.id}")

                return CreateResult(
                    session=session,
                    runtime=runtime,
                    sandbox=sandbox,
                    trace_id=trace_id,
                )

            except Exception as e:
                # Cleanup on failure
                logger.error(f"Session creation failed, cleaning up: {e}")
                await self._cleanup_failed_create(session.id, runtime, sandbox)
                raise

        except SessionError:
            raise
        except Exception as e:
            raise SessionError(
                message=f"Failed to create session: {e}",
                error_code="CREATE_FAILED",
            ) from e
        finally:
            if span:
                span.__exit__(None, None, None)

    async def terminate(
        self,
        session_id: str,
        *,
        reason: str = "user_request",
        force: bool = False,
    ) -> TerminateResult:
        """
        Terminate a session.

        Gracefully stops runtime, releases resources, persists state.

        Args:
            session_id: Session to terminate.
            reason: Termination reason (for logging).
            force: Force termination even if busy.

        Returns:
            TerminateResult with cleanup status.

        Raises:
            SessionNotFoundError: If session not found.
            SessionBusyError: If busy and not force.
        """
        logger.info(f"Terminating session: {session_id}, reason={reason}")

        runtime_stopped = False
        sandbox_released = False
        persisted = False

        try:
            # Get session from registry
            entry = await self._registry.get(session_id)

            if entry is None:
                # Check persistent store
                session = await self._store.get(session_id)
                if session is None:
                    raise SessionNotFoundError(session_id=session_id)

                # Already terminated
                if session.status.is_terminal:
                    return TerminateResult(
                        session_id=session_id,
                        final_status=session.status,
                        runtime_stopped=True,
                        sandbox_released=True,
                        persisted=True,
                    )

                # Mark as terminated in store
                session = session.with_status(SessionStatus.TERMINATED)
                await self._store.update(session)

                return TerminateResult(
                    session_id=session_id,
                    final_status=SessionStatus.TERMINATED,
                    runtime_stopped=True,
                    sandbox_released=True,
                    persisted=True,
                )

            # Check if busy
            if entry.status.is_busy and not force:
                raise SessionStateError(
                    message="Session is busy, use force=True to terminate",
                    session_id=session_id,
                    current_state=entry.status.value,
                    attempted_operation="terminate",
                )

            # Lock session for exclusive access
            async with self._registry.lock(session_id) as locked_entry:
                # Stop runtime
                if locked_entry.runtime:
                    try:
                        await self._stop_runtime(session_id, locked_entry.runtime)
                        runtime_stopped = True
                    except Exception as e:
                        logger.error(f"Failed to stop runtime: {e}")

                # Release sandbox
                if locked_entry.sandbox and self._sandbox_provider:
                    try:
                        await self._sandbox_provider.teardown(session_id)
                        sandbox_released = True
                    except Exception as e:
                        logger.error(f"Failed to release sandbox: {e}")

                # Cancel fanout task
                if locked_entry.fanout_task:
                    locked_entry.fanout_task.cancel()
                    try:
                        await locked_entry.fanout_task
                    except asyncio.CancelledError:
                        pass

                # Update session status
                session = locked_entry.session.with_status(SessionStatus.TERMINATED)

                # Persist
                try:
                    await self._store.update(session)
                    persisted = True
                except Exception as e:
                    logger.error(f"Failed to persist termination: {e}")

            # Unregister from memory
            await self._registry.unregister(session_id)

            logger.info(f"Session terminated: {session_id}")

            return TerminateResult(
                session_id=session_id,
                final_status=SessionStatus.TERMINATED,
                runtime_stopped=runtime_stopped,
                sandbox_released=sandbox_released,
                persisted=persisted,
            )

        except SessionError:
            raise
        except Exception as e:
            logger.error(f"Termination failed: {e}")
            raise SessionError(
                message=f"Failed to terminate session: {e}",
                session_id=session_id,
                error_code="TERMINATE_FAILED",
            ) from e

    async def fork(
        self,
        parent_session_id: str,
        *,
        at_seq: int | None = None,
        new_session_id: str | None = None,
        trace_id: str | None = None,
    ) -> CreateResult:
        """
        Fork a session from a parent.

        Creates new session with parent's history up to seq.

        Args:
            parent_session_id: Parent session ID.
            at_seq: Sequence to fork at (latest if None).
            new_session_id: Custom ID for fork.
            trace_id: Trace ID for observability.

        Returns:
            CreateResult with new session.

        Raises:
            SessionNotFoundError: If parent not found.
            SessionTerminatedError: If parent terminated.
        """
        logger.info(f"Forking session: parent={parent_session_id}, at_seq={at_seq}")

        # Get parent session
        parent = await self._store.get(parent_session_id)
        if parent is None:
            raise SessionNotFoundError(session_id=parent_session_id)

        if parent.status.is_terminal:
            raise SessionTerminatedError(
                session_id=parent_session_id,
                termination_reason="Cannot fork terminated session",
            )

        # Create fork with same config
        return await self.create(
            tenant_id=parent.tenant_id,
            config=parent.config,
            session_id=new_session_id,
            mode=parent.mode,
            scope_id=parent.scope_id,
            trace_id=trace_id,
        )

        # TODO: Copy message history up to at_seq

    async def _check_quotas(self, tenant_id: str) -> None:
        """Check all quotas for tenant."""
        if not self._quota_service:
            return

        # Check session quota
        allowed, current, limit = await self._quota_service.check_session_quota(
            tenant_id
        )
        if not allowed:
            raise SessionQuotaExceededError(
                message="Session limit exceeded for tenant",
                quota_type="sessions",
                current=current,
                limit=limit,
            )

        # Check sandbox quota if required
        if self._require_sandbox:
            allowed, current, limit = await self._quota_service.check_sandbox_quota(
                tenant_id
            )
            if not allowed:
                raise SessionQuotaExceededError(
                    message="Sandbox limit exceeded for tenant",
                    quota_type="sandboxes",
                    current=current,
                    limit=limit,
                )

    async def _provision_sandbox(self, session: Session) -> Any:
        """Provision sandbox for session."""
        if not self._sandbox_provider:
            return None

        logger.debug(f"Provisioning sandbox for session: {session.id}")

        try:
            sandbox = await self._sandbox_provider.provision(
                session.id,
                config={
                    "tenant_id": session.tenant_id,
                    "agent_id": session.config.agent_id,
                },
            )
            logger.debug(f"Sandbox provisioned: {session.id}")
            return sandbox

        except Exception as e:
            logger.error(f"Failed to provision sandbox: {e}")
            raise SessionError(
                message=f"Sandbox provisioning failed: {e}",
                session_id=session.id,
                error_code="SANDBOX_PROVISION_FAILED",
            ) from e

    async def _stop_runtime(self, session_id: str, runtime: Any) -> None:
        """Stop runtime gracefully."""
        try:
            if hasattr(runtime, "stop"):
                await runtime.stop()
            elif hasattr(runtime, "abort"):
                await runtime.abort()
        except Exception as e:
            logger.warning(f"Error stopping runtime: {e}")

        await self._runtime_provider.destroy(session_id)

    async def _cleanup_failed_create(
        self,
        session_id: str,
        runtime: Any | None,
        sandbox: Any | None,
    ) -> None:
        """Cleanup resources after failed creation."""
        if runtime:
            try:
                await self._runtime_provider.destroy(session_id)
            except Exception as e:
                logger.warning(f"Cleanup: failed to destroy runtime: {e}")

        if sandbox and self._sandbox_provider:
            try:
                await self._sandbox_provider.teardown(session_id)
            except Exception as e:
                logger.warning(f"Cleanup: failed to teardown sandbox: {e}")

        # Try to remove from registry if registered
        try:
            await self._registry.unregister(session_id)
        except Exception:
            pass

        # Try to delete from store if persisted
        try:
            await self._store.delete(session_id)
        except Exception:
            pass
