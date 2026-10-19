"""
Turn executor service.

Handles message sending and turn execution with:
- Idempotency via command_id
- Budget enforcement
- Timeout management
- Event streaming
- Error recovery

Single responsibility: Execute turns only.

Design Principles:
    1. Idempotent: Same command_id returns same result
    2. Observable: Events streamed in real-time
    3. Bounded: Timeouts and iteration limits
    4. Recoverable: Checkpoints for crash recovery

Usage:
    executor = TurnExecutor(
        registry=registry,
        store=session_store,
        message_store=message_store,
        event_sink=event_sink,
    )

    # Execute turn
    result = await executor.execute(
        session_id="ses_123",
        message="Hello, agent!",
        command_id="cmd_456",
    )

    # Abort running turn
    await executor.abort(session_id)
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from supervisor.core.session.models import (
    Session,
    SessionStatus,
    Turn,
    Message,
)
from supervisor.core.session.registry import SessionRegistry
from supervisor.core.session.protocols import (
    SessionStore,
    MessageStore,
    EventSink,
    QuotaService,
    TracingService,
)
from supervisor.core.session.errors import (
    SessionError,
    SessionNotFoundError,
    SessionTerminatedError,
    SessionBusyError,
    SessionQuotaExceededError,
    SessionTimeoutError,
    SessionStateError,
)

logger = logging.getLogger(__name__)


@dataclass
class TurnResult:
    """Result of turn execution."""

    turn_id: str
    session_id: str
    success: bool
    output: str
    tool_calls_count: int = 0
    tokens_used: int = 0
    duration_seconds: float = 0.0
    stop_reason: str = "completed"
    error: str | None = None
    needs_approval: bool = False
    approval_id: str | None = None


@dataclass
class ExecutionContext:
    """Context for turn execution."""

    session_id: str
    turn_id: str
    command_id: str
    trace_id: str | None
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Execution state
    aborted: bool = False
    checkpoint_seq: int = 0


class CommandDeduplicator:
    """
    Command deduplication for idempotency.

    Ensures same command_id returns same result.
    """

    def __init__(self, ttl_seconds: float = 3600.0) -> None:
        """
        Initialize deduplicator.

        Args:
            ttl_seconds: How long to cache results.
        """
        self._cache: dict[str, TurnResult] = {}
        self._timestamps: dict[str, float] = {}
        self._ttl = ttl_seconds
        self._lock = asyncio.Lock()

    async def get(self, command_id: str) -> TurnResult | None:
        """Get cached result for command."""
        async with self._lock:
            self._cleanup_expired()
            return self._cache.get(command_id)

    async def set(self, command_id: str, result: TurnResult) -> None:
        """Cache result for command."""
        async with self._lock:
            self._cache[command_id] = result
            self._timestamps[command_id] = time.monotonic()

    async def is_pending(self, command_id: str) -> bool:
        """Check if command is currently executing."""
        async with self._lock:
            return command_id in self._timestamps and command_id not in self._cache

    async def mark_pending(self, command_id: str) -> bool:
        """
        Mark command as pending.

        Returns False if already pending/completed.
        """
        async with self._lock:
            if command_id in self._timestamps:
                return False
            self._timestamps[command_id] = time.monotonic()
            return True

    def _cleanup_expired(self) -> None:
        """Remove expired entries."""
        now = time.monotonic()
        expired = [
            k for k, v in self._timestamps.items()
            if now - v > self._ttl
        ]
        for k in expired:
            self._cache.pop(k, None)
            self._timestamps.pop(k, None)


class TurnExecutor:
    """
    Turn execution service.

    Handles message sending with idempotency,
    budget enforcement, and event streaming.
    """

    def __init__(
        self,
        *,
        registry: SessionRegistry,
        store: SessionStore,
        message_store: MessageStore | None = None,
        event_sink: EventSink | None = None,
        quota_service: QuotaService | None = None,
        tracing: TracingService | None = None,
        turn_timeout_seconds: float = 300.0,
        max_concurrent_turns: int = 100,
    ) -> None:
        """
        Initialize executor.

        Args:
            registry: Session registry.
            store: Session store.
            message_store: Message history store.
            event_sink: Event streaming sink.
            quota_service: Quota enforcement.
            tracing: Tracing service.
            turn_timeout_seconds: Default turn timeout.
            max_concurrent_turns: Max concurrent executions.
        """
        self._registry = registry
        self._store = store
        self._message_store = message_store
        self._event_sink = event_sink
        self._quota_service = quota_service
        self._tracing = tracing
        self._turn_timeout = turn_timeout_seconds

        # Concurrency control
        self._semaphore = asyncio.Semaphore(max_concurrent_turns)
        self._active_turns: dict[str, ExecutionContext] = {}
        self._lock = asyncio.Lock()

        # Command deduplication
        self._dedup = CommandDeduplicator()

    async def execute(
        self,
        session_id: str,
        message: str,
        *,
        command_id: str | None = None,
        trace_id: str | None = None,
        timeout_seconds: float | None = None,
    ) -> TurnResult:
        """
        Execute a turn.

        Sends message to runtime and returns result.
        Idempotent: same command_id returns cached result.

        Args:
            session_id: Session identifier.
            message: User message.
            command_id: Idempotency key (auto-generated if None).
            trace_id: Trace ID for observability.
            timeout_seconds: Turn timeout (uses default if None).

        Returns:
            TurnResult with output and metrics.

        Raises:
            SessionNotFoundError: If session not found.
            SessionTerminatedError: If session terminated.
            SessionBusyError: If turn already in progress.
            SessionQuotaExceededError: If budget exceeded.
            SessionTimeoutError: If turn times out.
        """
        effective_command_id = command_id or f"cmd_{uuid.uuid4().hex[:16]}"
        effective_timeout = timeout_seconds or self._turn_timeout

        # Check for cached result (idempotency)
        cached = await self._dedup.get(effective_command_id)
        if cached:
            logger.debug(f"Returning cached result for command: {effective_command_id}")
            return cached

        # Check if command is pending
        if await self._dedup.is_pending(effective_command_id):
            raise SessionBusyError(
                message="Command already in progress",
                session_id=session_id,
                reason="duplicate_command",
            )

        # Mark command as pending
        if not await self._dedup.mark_pending(effective_command_id):
            # Race condition - return cached if available
            cached = await self._dedup.get(effective_command_id)
            if cached:
                return cached
            raise SessionBusyError(
                message="Command already in progress",
                session_id=session_id,
                reason="duplicate_command",
            )

        # Acquire concurrency semaphore
        async with self._semaphore:
            try:
                result = await self._execute_turn(
                    session_id=session_id,
                    message=message,
                    command_id=effective_command_id,
                    trace_id=trace_id,
                    timeout_seconds=effective_timeout,
                )

                # Cache result
                await self._dedup.set(effective_command_id, result)
                return result

            except Exception as e:
                # Cache error result for idempotency
                error_result = TurnResult(
                    turn_id="",
                    session_id=session_id,
                    success=False,
                    output="",
                    error=str(e),
                    stop_reason="error",
                )
                await self._dedup.set(effective_command_id, error_result)
                raise

    async def _execute_turn(
        self,
        session_id: str,
        message: str,
        command_id: str,
        trace_id: str | None,
        timeout_seconds: float,
    ) -> TurnResult:
        """Internal turn execution."""
        start_time = time.monotonic()

        # Get session entry
        entry = await self._registry.get(session_id)
        if entry is None:
            # Try to load from store (cold session)
            session = await self._store.get(session_id)
            if session is None:
                raise SessionNotFoundError(session_id=session_id)
            if session.status.is_terminal:
                raise SessionTerminatedError(session_id=session_id)

            # Need to resume cold session
            raise SessionStateError(
                message="Session is cold, needs recovery",
                session_id=session_id,
                current_state="cold",
                attempted_operation="send",
            )

        # Validate session state
        session = entry.session

        if session.status.is_terminal:
            raise SessionTerminatedError(session_id=session_id)

        if session.status.is_busy:
            raise SessionBusyError(
                message="Session is busy",
                session_id=session_id,
                reason=session.status.value,
            )

        # Check budget
        await self._check_budget(session)

        # Lock session for turn execution
        async with self._registry.lock(session_id) as locked_entry:
            # Re-validate state
            if locked_entry.session.status.is_busy:
                raise SessionBusyError(
                    message="Session became busy",
                    session_id=session_id,
                    reason=locked_entry.session.status.value,
                )

            # Create turn
            turn_number = locked_entry.session.metrics.turn_count + 1
            turn = Turn.create(session_id, turn_number, message)

            # Create execution context
            ctx = ExecutionContext(
                session_id=session_id,
                turn_id=turn.id,
                command_id=command_id,
                trace_id=trace_id,
            )

            # Track active turn
            async with self._lock:
                self._active_turns[session_id] = ctx

            try:
                # Update session status
                locked_entry.session = locked_entry.session.with_status(
                    SessionStatus.PROCESSING
                )
                await self._registry.update(locked_entry)

                # Emit turn start event
                await self._emit_event(
                    session_id,
                    "turn_start",
                    {"turn_id": turn.id, "turn_number": turn_number},
                    turn_id=turn.id,
                )

                # Execute via runtime
                result = await self._run_turn(
                    entry=locked_entry,
                    turn=turn,
                    ctx=ctx,
                    timeout_seconds=timeout_seconds,
                )

                # Update session metrics
                duration = time.monotonic() - start_time
                locked_entry.session = locked_entry.session.with_turn_completed(
                    tokens_used=result.tokens_used,
                    tool_calls=result.tool_calls_count,
                    messages_added=2,  # user + assistant
                    duration_seconds=duration,
                )

                # Update status based on result
                if result.needs_approval:
                    locked_entry.session = locked_entry.session.with_status(
                        SessionStatus.AWAITING_APPROVAL
                    )
                else:
                    locked_entry.session = locked_entry.session.with_status(
                        SessionStatus.ACTIVE
                    )

                await self._registry.update(locked_entry)
                await self._store.update(locked_entry.session)

                # Emit turn end event
                await self._emit_event(
                    session_id,
                    "turn_end",
                    {
                        "turn_id": turn.id,
                        "success": result.success,
                        "tokens_used": result.tokens_used,
                        "stop_reason": result.stop_reason,
                    },
                    turn_id=turn.id,
                )

                result.duration_seconds = duration
                return result

            except asyncio.TimeoutError:
                raise SessionTimeoutError(
                    session_id=session_id,
                    operation="turn",
                    timeout_seconds=timeout_seconds,
                )
            except Exception as e:
                # Emit error event
                await self._emit_event(
                    session_id,
                    "turn_error",
                    {"turn_id": turn.id, "error": str(e)},
                    turn_id=turn.id,
                )

                # Reset to active status
                locked_entry.session = locked_entry.session.with_status(
                    SessionStatus.ACTIVE,
                    error=str(e),
                )
                await self._registry.update(locked_entry)
                raise
            finally:
                # Remove from active turns
                async with self._lock:
                    self._active_turns.pop(session_id, None)

    async def _run_turn(
        self,
        entry: Any,
        turn: Turn,
        ctx: ExecutionContext,
        timeout_seconds: float,
    ) -> TurnResult:
        """Run turn via runtime."""
        runtime = entry.runtime

        if runtime is None:
            raise SessionStateError(
                message="No runtime available",
                session_id=ctx.session_id,
                current_state="no_runtime",
                attempted_operation="send",
            )

        # Collect events from runtime
        output_parts: list[str] = []
        tool_calls_count = 0
        tokens_used = 0
        stop_reason = "completed"
        needs_approval = False
        approval_id: str | None = None

        try:
            # Send message to runtime with timeout
            await asyncio.wait_for(
                runtime.send(
                    turn.user_message,
                    command_id=ctx.command_id,
                    mode="default",
                ),
                timeout=timeout_seconds,
            )

            # Collect events
            async for event in self._collect_events(runtime, ctx, timeout_seconds):
                event_type = event.get("type", "")
                payload = event.get("payload", {})

                # Stream event to subscribers
                await self._emit_event(
                    ctx.session_id,
                    event_type,
                    payload,
                    turn_id=ctx.turn_id,
                )

                # Track metrics
                if event_type == "message_end":
                    content = payload.get("content", "")
                    if content:
                        output_parts.append(content)
                    tokens_used += payload.get("usage", {}).get("total_tokens", 0)

                elif event_type == "tool_end":
                    tool_calls_count += 1

                elif event_type == "run_end":
                    stop_reason = payload.get("stop_reason", "completed")

                elif event_type == "approval_required":
                    needs_approval = True
                    approval_id = payload.get("approval_id")
                    stop_reason = "approval_required"

                # Check for abort
                if ctx.aborted:
                    stop_reason = "aborted"
                    break

            return TurnResult(
                turn_id=ctx.turn_id,
                session_id=ctx.session_id,
                success=True,
                output="\n".join(output_parts),
                tool_calls_count=tool_calls_count,
                tokens_used=tokens_used,
                stop_reason=stop_reason,
                needs_approval=needs_approval,
                approval_id=approval_id,
            )

        except asyncio.TimeoutError:
            return TurnResult(
                turn_id=ctx.turn_id,
                session_id=ctx.session_id,
                success=False,
                output="",
                stop_reason="timeout",
                error=f"Turn timed out after {timeout_seconds}s",
            )
        except Exception as e:
            return TurnResult(
                turn_id=ctx.turn_id,
                session_id=ctx.session_id,
                success=False,
                output="",
                stop_reason="error",
                error=str(e),
            )

    async def _collect_events(
        self,
        runtime: Any,
        ctx: ExecutionContext,
        timeout_seconds: float,
    ) -> AsyncIterator[dict[str, Any]]:
        """Collect events from runtime."""
        if not hasattr(runtime, "events"):
            return

        try:
            async for envelope in runtime.events():
                if ctx.aborted:
                    break

                yield {
                    "type": envelope.type,
                    "payload": envelope.payload if hasattr(envelope, "payload") else {},
                    "seq": envelope.seq if hasattr(envelope, "seq") else 0,
                    "turn_id": ctx.turn_id,
                }

                # Check for terminal events
                if envelope.type in ("run_end", "error"):
                    break

        except asyncio.CancelledError:
            pass

    async def abort(self, session_id: str) -> bool:
        """
        Abort running turn.

        Args:
            session_id: Session identifier.

        Returns:
            True if turn was aborted, False if no turn running.
        """
        async with self._lock:
            ctx = self._active_turns.get(session_id)
            if ctx is None:
                return False
            ctx.aborted = True

        # Try to abort runtime
        entry = await self._registry.get(session_id)
        if entry and entry.runtime and hasattr(entry.runtime, "abort"):
            try:
                await entry.runtime.abort()
            except Exception as e:
                logger.warning(f"Failed to abort runtime: {e}")

        logger.info(f"Aborted turn: session={session_id}")
        return True

    async def _check_budget(self, session: Session) -> None:
        """Check token budget."""
        if not self._quota_service:
            return

        # Estimate tokens for turn
        estimated = 1000  # Conservative estimate

        allowed, current, limit = await self._quota_service.check_token_budget(
            session.id,
            estimated,
        )

        if not allowed:
            raise SessionQuotaExceededError(
                message="Token budget exceeded",
                session_id=session.id,
                quota_type="tokens",
                current=current,
                limit=limit,
            )

    async def _emit_event(
        self,
        session_id: str,
        event_type: str,
        payload: dict[str, Any],
        turn_id: str | None = None,
    ) -> None:
        """Emit event to sink."""
        if not self._event_sink:
            return

        try:
            await self._event_sink.emit(
                session_id,
                event_type,
                payload,
                turn_id=turn_id,
            )
        except Exception as e:
            logger.warning(f"Failed to emit event: {e}")

    def get_active_turns(self) -> dict[str, ExecutionContext]:
        """Get currently active turns."""
        return dict(self._active_turns)

    def is_executing(self, session_id: str) -> bool:
        """Check if session has active turn."""
        return session_id in self._active_turns
