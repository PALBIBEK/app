"""Protocol interfaces for session layer dependency injection.

Protocols define the contracts that implementations must fulfill,
enabling loose coupling and easy testing through mocks.

All protocols use @runtime_checkable for isinstance() support.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from supervisor.session.core.types import (
        ResumeDecision,
        ResumeResult,
        SessionInfo,
        ToolInvokeOutcome,
    )

__all__ = [
    "RecoveryProtocol",
    "SessionManagerProtocol",
    "SessionRegistryProtocol",
    "SessionRowView",
]


# =============================================================================
# Session Row Protocol
# =============================================================================


@runtime_checkable
class SessionRowView(Protocol):
    """Interface for session database row access.

    Used by recovery to inspect session state without
    depending on SQLAlchemy models directly.
    """

    @property
    def harness_version(self) -> str:
        """The pinned harness version for this session."""
        ...

    @property
    def runtime(self) -> str:
        """The runtime name (echo, harness, githi)."""
        ...

    @property
    def status(self) -> str:
        """Current session status (active, terminated, etc.)."""
        ...

    @property
    def manifest(self) -> dict[str, Any] | None:
        """The pinned harness manifest snapshot."""
        ...


# =============================================================================
# Registry Protocol
# =============================================================================


@runtime_checkable
class SessionRegistryProtocol(Protocol):
    """Interface for session-to-runtime mapping.

    The registry tracks which sessions have active runtimes
    in memory. Thread-safe operations required.
    """

    def get(self, session_id: str) -> Any | None:
        """Retrieve runtime by session ID.

        Args:
            session_id: The session identifier

        Returns:
            AgentRuntime if found, None otherwise
        """
        ...

    def put(self, session_id: str, runtime: Any) -> None:
        """Store a runtime instance.

        Args:
            session_id: The session identifier
            runtime: AgentRuntime instance
        """
        ...

    def remove(self, session_id: str) -> Any | None:
        """Remove and return a runtime.

        Args:
            session_id: The session identifier

        Returns:
            AgentRuntime if found and removed, None otherwise
        """
        ...

    def all(self) -> list[str]:
        """List all registered session IDs.

        Returns:
            List of session IDs with active runtimes
        """
        ...


# =============================================================================
# Recovery Protocol
# =============================================================================


@runtime_checkable
class RecoveryProtocol(Protocol):
    """Interface for session recovery decisions.

    The recovery engine determines whether and how a session
    can be resumed after restart.
    """

    async def decide(
        self,
        session_id: str,
        *,
        session_row: SessionRowView,
        sandbox_resumable: bool = True,
    ) -> ResumeDecision:
        """Determine how a session should be resumed.

        Args:
            session_id: The session to evaluate
            session_row: Database row with session state
            sandbox_resumable: Whether sandbox can be resumed

        Returns:
            ResumeDecision with state and metadata

        Decision Logic:
            1. If session in registry → WARM
            2. Check unpaired tools → apply recovery policy
            3. Check unpaired compactions → NEEDS_REVIEW
            4. Check sandbox resumability → COLD or UNRECOVERABLE
        """
        ...


# =============================================================================
# Session Manager Protocol
# =============================================================================


@runtime_checkable
class SessionManagerProtocol(Protocol):
    """Interface for session lifecycle management.

    The session manager orchestrates all session operations
    including creation, messaging, and termination.
    """

    async def create(
        self,
        *,
        tenant_id: str,
        harness_version: str,
        runtime_name: str = "echo",
        **kwargs: Any,
    ) -> str:
        """Create a new session.

        Args:
            tenant_id: Tenant identifier
            harness_version: Harness version to use
            runtime_name: Runtime implementation name
            **kwargs: Additional session configuration

        Returns:
            New session ID

        Raises:
            IncompatibleHarness: If harness not compatible
            QuotaExceeded: If tenant quota reached
        """
        ...

    async def resume(self, session_id: str) -> ResumeResult:
        """Resume a cold or paused session.

        Args:
            session_id: Session to resume

        Returns:
            ResumeResult with state and any issues

        Raises:
            SessionNotFound: If session doesn't exist
            RecoveryBlocked: If session needs review
        """
        ...

    async def terminate(self, session_id: str) -> None:
        """Terminate a session and cleanup resources.

        Args:
            session_id: Session to terminate

        Raises:
            SessionNotFound: If session doesn't exist
        """
        ...

    async def send(
        self,
        session_id: str,
        text: str,
        *,
        command_id: str,
    ) -> None:
        """Send a user message to a session.

        Args:
            session_id: Target session
            text: Message content
            command_id: Unique command identifier

        Raises:
            SessionNotFound: If session doesn't exist
            SessionTerminated: If session is terminated
            BudgetExceeded: If cost limit reached
        """
        ...

    async def abort(self, session_id: str) -> None:
        """Abort the current turn in a session.

        Args:
            session_id: Session to abort

        Raises:
            SessionNotFound: If session doesn't exist
        """
        ...

    async def get(self, session_id: str) -> SessionInfo:
        """Get session information.

        Args:
            session_id: Session to query

        Returns:
            SessionInfo with current state

        Raises:
            SessionNotFound: If session doesn't exist
        """
        ...

    async def invoke_tool(
        self,
        session_id: str,
        tool_call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> ToolInvokeOutcome:
        """Invoke a tool in a session.

        Args:
            session_id: Session context
            tool_call_id: Unique tool call identifier
            tool_name: Tool to invoke
            arguments: Tool arguments

        Returns:
            ToolInvokeOutcome with result

        Raises:
            SessionNotFound: If session doesn't exist
            ToolInvokeNotConfigured: If tools not configured
        """
        ...
