"""
Long-lived agent session workflow.

This workflow represents a single agent session and can run for hours, days,
or even weeks. It survives process restarts, deployments, and infrastructure
failures through Temporal's durable execution guarantees.

Key Properties:
    - One workflow per session (workflow ID = session ID)
    - Receives user messages via signals (async, non-blocking)
    - Spawns child workflows for turn execution
    - Pauses for human approvals (can wait indefinitely)
    - Maintains session state across restarts

State Management:
    - Messages queue: pending user messages
    - Approval state: waiting for human decision
    - Turn history: completed turn results
    - Session status: active, paused, stopped, failed

Signals:
    - send_message(text): Queue user message for processing
    - approve(approval_id, decision): Respond to approval request
    - stop(): Gracefully terminate session

Queries:
    - get_status(): Current session status
    - get_pending_approval(): Active approval request
    - get_turn_count(): Number of completed turns

Workflow ID Convention:
    f"agent-session-{session_id}"

Example:
    ```python
    # Start session workflow
    handle = await client.start_workflow(
        AgentSessionWorkflow.run,
        args=[session_id, config],
        id=f"agent-session-{session_id}",
        task_queue="agent-sessions",
    )

    # Send message
    await handle.signal(AgentSessionWorkflow.send_message, "Hello, agent!")

    # Query status
    status = await handle.query(AgentSessionWorkflow.get_status)

    # Stop session
    await handle.signal(AgentSessionWorkflow.stop)
    ```
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta
from enum import Enum
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import (
    ActivityError,
    ChildWorkflowError,
    ApplicationError,
)

with workflow.unsafe.imports_passed_through():
    import logging
    from supervisor.temporal.workflows.turn import AgentTurnWorkflow

logger = logging.getLogger(__name__)


class SessionStatus(str, Enum):
    """Session lifecycle states."""

    INITIALIZING = "initializing"
    ACTIVE = "active"
    PROCESSING = "processing"  # Turn in progress
    AWAITING_APPROVAL = "awaiting_approval"
    PAUSED = "paused"
    STOPPED = "stopped"
    FAILED = "failed"
    COMPLETED = "completed"


@dataclass
class SessionConfig:
    """
    Configuration for agent session.

    Passed when starting the workflow.
    """

    tenant_id: str
    agent_id: str
    model_id: str = "gpt-4"
    max_turns: int = 1000
    turn_timeout_seconds: float = 300.0  # 5 minutes per turn
    session_timeout_hours: float = 24.0  # Max session lifetime
    max_tool_iterations: int = 25
    checkpoint_interval: int = 1  # Checkpoint every N tool calls
    tools_enabled: list[str] = field(default_factory=list)
    system_prompt: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ApprovalRequest:
    """Pending approval request."""

    approval_id: str
    turn_id: str
    prompt: str
    options: list[str]
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    requested_at: float = 0.0  # workflow.now() timestamp


@dataclass
class TurnResult:
    """Result of a completed turn."""

    turn_id: str
    turn_number: int
    input_message: str
    output_message: str
    tool_calls_count: int
    tokens_used: int
    duration_seconds: float
    stop_reason: str


@dataclass
class SessionState:
    """
    Internal session state.

    Persisted in workflow history, survives replays.
    """

    status: SessionStatus = SessionStatus.INITIALIZING
    turn_count: int = 0
    total_tokens: int = 0
    pending_messages: list[str] = field(default_factory=list)
    pending_approval: ApprovalRequest | None = None
    approval_response: tuple[str, str] | None = None  # (approval_id, decision)
    turn_results: list[TurnResult] = field(default_factory=list)
    error_message: str | None = None
    stop_requested: bool = False


@workflow.defn
class AgentSessionWorkflow:
    """
    Long-lived workflow managing a single agent session.

    This workflow:
    1. Waits for user messages via signals
    2. Executes each turn as a child workflow
    3. Handles approvals (pausing for human input)
    4. Continues until stopped or max turns reached

    The workflow can run for extended periods (hours/days) and survives
    process restarts, deployments, and infrastructure failures.
    """

    def __init__(self) -> None:
        """Initialize session state."""
        self._state = SessionState()
        self._config: SessionConfig | None = None
        self._session_id: str = ""

    @workflow.run
    async def run(
        self,
        session_id: str,
        config: SessionConfig,
    ) -> dict[str, Any]:
        """
        Main workflow execution.

        Args:
            session_id: Unique session identifier.
            config: Session configuration.

        Returns:
            Final session summary.
        """
        self._session_id = session_id
        self._config = config

        workflow.logger.info(
            f"Starting session workflow: session_id={session_id}, "
            f"tenant={config.tenant_id}, agent={config.agent_id}"
        )

        # Initialize session
        try:
            await self._initialize_session()
        except Exception as e:
            self._state.status = SessionStatus.FAILED
            self._state.error_message = f"Initialization failed: {e}"
            return self._build_summary()

        self._state.status = SessionStatus.ACTIVE

        # Main message loop
        try:
            await self._message_loop()
        except Exception as e:
            workflow.logger.error(f"Session failed: {e}")
            self._state.status = SessionStatus.FAILED
            self._state.error_message = str(e)

        return self._build_summary()

    async def _initialize_session(self) -> None:
        """Initialize session resources via activity."""
        await workflow.execute_activity(
            "initialize_session",
            args=[self._session_id, self._config],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(
                maximum_attempts=3,
                initial_interval=timedelta(seconds=1),
            ),
        )

    async def _message_loop(self) -> None:
        """
        Main message processing loop.

        Waits for messages, processes turns, handles approvals.
        Continues until stopped or max turns reached.
        """
        assert self._config is not None

        while not self._state.stop_requested:
            # Check turn limit
            if self._state.turn_count >= self._config.max_turns:
                workflow.logger.info(f"Max turns reached: {self._config.max_turns}")
                self._state.status = SessionStatus.COMPLETED
                break

            # Wait for message or stop signal
            await workflow.wait_condition(
                lambda: (
                    len(self._state.pending_messages) > 0
                    or self._state.stop_requested
                    or self._state.approval_response is not None
                )
            )

            # Handle stop request
            if self._state.stop_requested:
                workflow.logger.info("Stop requested, exiting message loop")
                self._state.status = SessionStatus.STOPPED
                break

            # Handle approval response (resume paused turn)
            if self._state.approval_response is not None:
                await self._handle_approval_response()
                continue

            # Process next message
            if self._state.pending_messages:
                message = self._state.pending_messages.pop(0)
                await self._process_turn(message)

    async def _process_turn(self, message: str) -> None:
        """
        Process a single user turn as a child workflow.

        Args:
            message: User's message text.
        """
        assert self._config is not None

        self._state.turn_count += 1
        turn_number = self._state.turn_count
        turn_id = f"{self._session_id}-turn-{turn_number}"

        workflow.logger.info(f"Starting turn {turn_number}: {message[:100]}...")
        self._state.status = SessionStatus.PROCESSING

        try:
            # Execute turn as child workflow
            result = await workflow.execute_child_workflow(
                AgentTurnWorkflow.run,
                args=[
                    self._session_id,
                    turn_id,
                    message,
                    {
                        "model_id": self._config.model_id,
                        "max_iterations": self._config.max_tool_iterations,
                        "checkpoint_interval": self._config.checkpoint_interval,
                        "tools_enabled": self._config.tools_enabled,
                    },
                ],
                id=turn_id,
                execution_timeout=timedelta(seconds=self._config.turn_timeout_seconds),
                retry_policy=RetryPolicy(
                    maximum_attempts=1,  # No automatic retry for turns
                ),
            )

            # Handle approval request from turn
            if result.get("needs_approval"):
                await self._request_approval(result, turn_id)
                return

            # Record successful turn
            self._record_turn_result(turn_id, turn_number, message, result)
            self._state.status = SessionStatus.ACTIVE

        except ChildWorkflowError as e:
            workflow.logger.error(f"Turn {turn_number} failed: {e}")
            # Don't fail entire session on turn failure
            self._state.status = SessionStatus.ACTIVE
            # Could emit error event here

    async def _request_approval(
        self,
        turn_result: dict[str, Any],
        turn_id: str,
    ) -> None:
        """
        Handle approval request from turn workflow.

        Pauses session until human responds.

        Args:
            turn_result: Result containing approval details.
            turn_id: Turn that requested approval.
        """
        approval_id = turn_result["approval_id"]
        prompt = turn_result.get("approval_prompt", "Approve this action?")
        options = turn_result.get("approval_options", ["approve", "deny"])
        tool_name = turn_result.get("tool_name")
        tool_args = turn_result.get("tool_args")

        self._state.pending_approval = ApprovalRequest(
            approval_id=approval_id,
            turn_id=turn_id,
            prompt=prompt,
            options=options,
            tool_name=tool_name,
            tool_args=tool_args,
            requested_at=workflow.now().timestamp(),
        )
        self._state.status = SessionStatus.AWAITING_APPROVAL

        workflow.logger.info(
            f"Awaiting approval: id={approval_id}, prompt={prompt[:50]}..."
        )

        # Persist approval request
        await workflow.execute_activity(
            "persist_approval_request",
            args=[self._session_id, self._state.pending_approval],
            start_to_close_timeout=timedelta(seconds=10),
        )

    async def _handle_approval_response(self) -> None:
        """Handle approval response and resume turn."""
        assert self._state.approval_response is not None
        assert self._state.pending_approval is not None

        approval_id, decision = self._state.approval_response
        pending = self._state.pending_approval

        # Validate approval ID
        if approval_id != pending.approval_id:
            workflow.logger.warning(
                f"Approval ID mismatch: expected {pending.approval_id}, got {approval_id}"
            )
            self._state.approval_response = None
            return

        workflow.logger.info(f"Approval received: id={approval_id}, decision={decision}")

        # Clear approval state
        self._state.pending_approval = None
        self._state.approval_response = None
        self._state.status = SessionStatus.PROCESSING

        # Resume turn with approval decision
        try:
            result = await workflow.execute_activity(
                "resume_turn_with_approval",
                args=[
                    self._session_id,
                    pending.turn_id,
                    approval_id,
                    decision,
                ],
                start_to_close_timeout=timedelta(seconds=300),
                heartbeat_timeout=timedelta(seconds=30),
            )

            # Check for additional approval requests
            if result.get("needs_approval"):
                await self._request_approval(result, pending.turn_id)
                return

            # Record completed turn
            self._record_turn_result(
                pending.turn_id,
                self._state.turn_count,
                "",  # Original message not available here
                result,
            )
            self._state.status = SessionStatus.ACTIVE

        except ActivityError as e:
            workflow.logger.error(f"Resume after approval failed: {e}")
            self._state.status = SessionStatus.ACTIVE

    def _record_turn_result(
        self,
        turn_id: str,
        turn_number: int,
        input_message: str,
        result: dict[str, Any],
    ) -> None:
        """Record completed turn result."""
        turn_result = TurnResult(
            turn_id=turn_id,
            turn_number=turn_number,
            input_message=input_message,
            output_message=result.get("output", ""),
            tool_calls_count=result.get("tool_calls_count", 0),
            tokens_used=result.get("tokens_used", 0),
            duration_seconds=result.get("duration_seconds", 0.0),
            stop_reason=result.get("stop_reason", "unknown"),
        )
        self._state.turn_results.append(turn_result)
        self._state.total_tokens += turn_result.tokens_used

        # Keep only recent turns in memory (older ones persisted)
        if len(self._state.turn_results) > 100:
            self._state.turn_results = self._state.turn_results[-100:]

    def _build_summary(self) -> dict[str, Any]:
        """Build final session summary."""
        return {
            "session_id": self._session_id,
            "status": self._state.status.value,
            "turn_count": self._state.turn_count,
            "total_tokens": self._state.total_tokens,
            "error_message": self._state.error_message,
            "recent_turns": [
                {
                    "turn_id": t.turn_id,
                    "turn_number": t.turn_number,
                    "tool_calls_count": t.tool_calls_count,
                    "tokens_used": t.tokens_used,
                    "stop_reason": t.stop_reason,
                }
                for t in self._state.turn_results[-10:]
            ],
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Signals - External events that modify workflow state
    # ─────────────────────────────────────────────────────────────────────────

    @workflow.signal
    async def send_message(self, message: str) -> None:
        """
        Signal to queue a user message.

        Args:
            message: User's message text.
        """
        if self._state.stop_requested:
            workflow.logger.warning("Message received after stop requested, ignoring")
            return

        if self._state.status == SessionStatus.FAILED:
            workflow.logger.warning("Message received after session failed, ignoring")
            return

        workflow.logger.info(f"Message queued: {message[:50]}...")
        self._state.pending_messages.append(message)

    @workflow.signal
    async def approve(self, approval_id: str, decision: str) -> None:
        """
        Signal to respond to an approval request.

        Args:
            approval_id: ID of the approval request.
            decision: User's decision (e.g., "approve", "deny").
        """
        if self._state.pending_approval is None:
            workflow.logger.warning(
                f"Approval received but no pending approval: {approval_id}"
            )
            return

        if self._state.pending_approval.approval_id != approval_id:
            workflow.logger.warning(
                f"Approval ID mismatch: expected {self._state.pending_approval.approval_id}, "
                f"got {approval_id}"
            )
            return

        workflow.logger.info(f"Approval signal received: {approval_id} -> {decision}")
        self._state.approval_response = (approval_id, decision)

    @workflow.signal
    async def stop(self) -> None:
        """Signal to gracefully stop the session."""
        workflow.logger.info("Stop signal received")
        self._state.stop_requested = True

    @workflow.signal
    async def pause(self) -> None:
        """Signal to pause the session (no new turns processed)."""
        if self._state.status == SessionStatus.ACTIVE:
            workflow.logger.info("Pausing session")
            self._state.status = SessionStatus.PAUSED

    @workflow.signal
    async def resume(self) -> None:
        """Signal to resume a paused session."""
        if self._state.status == SessionStatus.PAUSED:
            workflow.logger.info("Resuming session")
            self._state.status = SessionStatus.ACTIVE

    # ─────────────────────────────────────────────────────────────────────────
    # Queries - Read-only state inspection
    # ─────────────────────────────────────────────────────────────────────────

    @workflow.query
    def get_status(self) -> dict[str, Any]:
        """
        Query current session status.

        Returns:
            Dict with status, turn_count, pending_messages, etc.
        """
        return {
            "status": self._state.status.value,
            "turn_count": self._state.turn_count,
            "total_tokens": self._state.total_tokens,
            "pending_messages_count": len(self._state.pending_messages),
            "has_pending_approval": self._state.pending_approval is not None,
            "error_message": self._state.error_message,
        }

    @workflow.query
    def get_pending_approval(self) -> dict[str, Any] | None:
        """
        Query pending approval request.

        Returns:
            Approval request details or None.
        """
        if self._state.pending_approval is None:
            return None

        return {
            "approval_id": self._state.pending_approval.approval_id,
            "turn_id": self._state.pending_approval.turn_id,
            "prompt": self._state.pending_approval.prompt,
            "options": self._state.pending_approval.options,
            "tool_name": self._state.pending_approval.tool_name,
            "tool_args": self._state.pending_approval.tool_args,
            "requested_at": self._state.pending_approval.requested_at,
        }

    @workflow.query
    def get_turn_count(self) -> int:
        """Query number of completed turns."""
        return self._state.turn_count

    @workflow.query
    def get_recent_turns(self) -> list[dict[str, Any]]:
        """Query recent turn results."""
        return [
            {
                "turn_id": t.turn_id,
                "turn_number": t.turn_number,
                "tool_calls_count": t.tool_calls_count,
                "tokens_used": t.tokens_used,
                "duration_seconds": t.duration_seconds,
                "stop_reason": t.stop_reason,
            }
            for t in self._state.turn_results[-20:]
        ]
