"""
Session protocols (abstractions).

Defines interfaces that session services depend on.
Implementations are injected, not imported directly.

This enables:
- Easy testing with mocks
- Swappable implementations
- Clear contracts between components

Protocols:
    - SessionStore: Persistent session storage
    - EventSink: Event streaming
    - RuntimeProvider: Runtime lifecycle
    - SandboxProvider: Sandbox lifecycle
    - ApprovalService: HITL approvals
"""

from __future__ import annotations

from abc import abstractmethod
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Protocol, runtime_checkable

from supervisor.core.session.models import (
    Session,
    SessionConfig,
    SessionStatus,
    Turn,
    Message,
)


@runtime_checkable
class SessionStore(Protocol):
    """
    Protocol for persistent session storage.

    Implementations: PostgreSQL, Redis, In-Memory.
    """

    @abstractmethod
    async def create(self, session: Session) -> None:
        """
        Create a new session.

        Args:
            session: Session to create.

        Raises:
            SessionAlreadyExistsError: If session ID exists.
        """
        ...

    @abstractmethod
    async def get(self, session_id: str) -> Session | None:
        """
        Get session by ID.

        Args:
            session_id: Session identifier.

        Returns:
            Session if found, None otherwise.
        """
        ...

    @abstractmethod
    async def update(self, session: Session) -> None:
        """
        Update existing session.

        Args:
            session: Session with updated state.

        Raises:
            SessionNotFoundError: If session doesn't exist.
        """
        ...

    @abstractmethod
    async def delete(self, session_id: str) -> None:
        """
        Delete a session.

        Args:
            session_id: Session to delete.
        """
        ...

    @abstractmethod
    async def list_by_tenant(
        self,
        tenant_id: str,
        *,
        status: SessionStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Session]:
        """
        List sessions for a tenant.

        Args:
            tenant_id: Tenant identifier.
            status: Filter by status (optional).
            limit: Maximum results.
            offset: Pagination offset.

        Returns:
            List of sessions.
        """
        ...

    @abstractmethod
    async def count_by_tenant(
        self,
        tenant_id: str,
        *,
        status: SessionStatus | None = None,
    ) -> int:
        """
        Count sessions for a tenant.

        Args:
            tenant_id: Tenant identifier.
            status: Filter by status (optional).

        Returns:
            Session count.
        """
        ...


@runtime_checkable
class MessageStore(Protocol):
    """
    Protocol for message history storage.

    Separate from SessionStore for scalability.
    """

    @abstractmethod
    async def append(self, session_id: str, messages: list[Message]) -> None:
        """
        Append messages to history.

        Args:
            session_id: Session identifier.
            messages: Messages to append.
        """
        ...

    @abstractmethod
    async def get_history(
        self,
        session_id: str,
        *,
        limit: int | None = None,
        since_seq: int = 0,
    ) -> list[Message]:
        """
        Get message history.

        Args:
            session_id: Session identifier.
            limit: Maximum messages (None = all).
            since_seq: Start from sequence number.

        Returns:
            Message history.
        """
        ...

    @abstractmethod
    async def get_count(self, session_id: str) -> int:
        """
        Get message count.

        Args:
            session_id: Session identifier.

        Returns:
            Number of messages.
        """
        ...


@runtime_checkable
class EventSink(Protocol):
    """
    Protocol for event streaming.

    Implementations: SSE, WebSocket, Kafka.
    """

    @abstractmethod
    async def emit(
        self,
        session_id: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        turn_id: str | None = None,
        seq: int | None = None,
    ) -> None:
        """
        Emit an event.

        Args:
            session_id: Session identifier.
            event_type: Event type name.
            payload: Event data.
            turn_id: Turn identifier (optional).
            seq: Sequence number (optional).
        """
        ...

    @abstractmethod
    async def subscribe(
        self,
        session_id: str,
    ) -> AsyncIterator[dict[str, Any]]:
        """
        Subscribe to session events.

        Args:
            session_id: Session to subscribe to.

        Yields:
            Event dictionaries.
        """
        ...


@runtime_checkable
class RuntimeProvider(Protocol):
    """
    Protocol for agent runtime lifecycle.

    Manages runtime instances (create, get, destroy).
    """

    @abstractmethod
    async def create(
        self,
        session: Session,
        *,
        sandbox: Any | None = None,
        seams: Any | None = None,
    ) -> Any:
        """
        Create a new runtime for session.

        Args:
            session: Session configuration.
            sandbox: Optional sandbox handle.
            seams: Optional seams configuration.

        Returns:
            Runtime instance.
        """
        ...

    @abstractmethod
    async def get(self, session_id: str) -> Any | None:
        """
        Get existing runtime.

        Args:
            session_id: Session identifier.

        Returns:
            Runtime if exists, None otherwise.
        """
        ...

    @abstractmethod
    async def destroy(self, session_id: str) -> None:
        """
        Destroy runtime.

        Args:
            session_id: Session identifier.
        """
        ...

    @abstractmethod
    async def send(
        self,
        session_id: str,
        message: str,
        *,
        command_id: str,
        trace_id: str | None = None,
    ) -> None:
        """
        Send message to runtime.

        Args:
            session_id: Session identifier.
            message: User message.
            command_id: Idempotency key.
            trace_id: Trace ID for observability.
        """
        ...


@runtime_checkable
class SandboxProvider(Protocol):
    """
    Protocol for sandbox lifecycle.

    Manages isolated execution environments.
    """

    @abstractmethod
    async def provision(
        self,
        session_id: str,
        config: dict[str, Any],
    ) -> Any:
        """
        Provision a sandbox.

        Args:
            session_id: Owning session.
            config: Sandbox configuration.

        Returns:
            Sandbox handle.
        """
        ...

    @abstractmethod
    async def teardown(self, session_id: str) -> None:
        """
        Teardown sandbox.

        Args:
            session_id: Session identifier.
        """
        ...

    @abstractmethod
    async def get(self, session_id: str) -> Any | None:
        """
        Get existing sandbox.

        Args:
            session_id: Session identifier.

        Returns:
            Sandbox if exists, None otherwise.
        """
        ...

    @abstractmethod
    async def health_check(self, session_id: str) -> bool:
        """
        Check sandbox health.

        Args:
            session_id: Session identifier.

        Returns:
            True if healthy.
        """
        ...


@runtime_checkable
class ApprovalService(Protocol):
    """
    Protocol for human-in-the-loop approvals.
    """

    @abstractmethod
    async def request(
        self,
        session_id: str,
        turn_id: str,
        *,
        prompt: str,
        options: list[str],
        tool_name: str | None = None,
        tool_args: dict[str, Any] | None = None,
        timeout_seconds: float = 3600.0,
    ) -> str:
        """
        Request approval (blocking until response).

        Args:
            session_id: Session identifier.
            turn_id: Turn identifier.
            prompt: Approval prompt.
            options: Available choices.
            tool_name: Tool requiring approval.
            tool_args: Tool arguments.
            timeout_seconds: Timeout for response.

        Returns:
            Approval ID.
        """
        ...

    @abstractmethod
    async def respond(
        self,
        approval_id: str,
        decision: str,
    ) -> None:
        """
        Respond to approval request.

        Args:
            approval_id: Approval identifier.
            decision: User's decision.
        """
        ...

    @abstractmethod
    async def list_pending(
        self,
        session_id: str,
    ) -> list[dict[str, Any]]:
        """
        List pending approvals.

        Args:
            session_id: Session identifier.

        Returns:
            List of pending approval requests.
        """
        ...


@runtime_checkable
class QuotaService(Protocol):
    """
    Protocol for quota enforcement.
    """

    @abstractmethod
    async def check_session_quota(
        self,
        tenant_id: str,
    ) -> tuple[bool, int, int]:
        """
        Check if tenant can create new session.

        Args:
            tenant_id: Tenant identifier.

        Returns:
            (allowed, current_count, limit)
        """
        ...

    @abstractmethod
    async def check_sandbox_quota(
        self,
        tenant_id: str,
    ) -> tuple[bool, int, int]:
        """
        Check if tenant can provision sandbox.

        Args:
            tenant_id: Tenant identifier.

        Returns:
            (allowed, current_count, limit)
        """
        ...

    @abstractmethod
    async def check_token_budget(
        self,
        session_id: str,
        estimated_tokens: int,
    ) -> tuple[bool, int, int]:
        """
        Check if session has token budget.

        Args:
            session_id: Session identifier.
            estimated_tokens: Estimated token usage.

        Returns:
            (allowed, current_usage, limit)
        """
        ...


@runtime_checkable
class TracingService(Protocol):
    """
    Protocol for distributed tracing.
    """

    @abstractmethod
    def start_span(
        self,
        name: str,
        *,
        trace_id: str | None = None,
        parent_span_id: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> Any:
        """
        Start a new span.

        Args:
            name: Span name.
            trace_id: Trace ID (generate if None).
            parent_span_id: Parent span ID.
            attributes: Span attributes.

        Returns:
            Span context manager.
        """
        ...

    @abstractmethod
    def inject_context(self) -> dict[str, str]:
        """
        Inject trace context for propagation.

        Returns:
            Context headers.
        """
        ...

    @abstractmethod
    def extract_context(self, headers: dict[str, str]) -> str | None:
        """
        Extract trace context from headers.

        Args:
            headers: Request headers.

        Returns:
            Trace ID if found.
        """
        ...
