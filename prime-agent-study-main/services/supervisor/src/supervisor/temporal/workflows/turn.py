"""
Agent turn workflow for single user message processing.

This child workflow handles a single user turn:
1. Load conversation context
2. Call LLM with available tools
3. Execute tool calls (with checkpointing)
4. Repeat until no more tool calls or limit reached
5. Persist final state

The workflow is bounded (has a defined end) and typically completes
within seconds to minutes. It's spawned as a child of AgentSessionWorkflow.

Key Properties:
    - Deterministic execution (survives replay)
    - Tool execution via activities (retriable, timeout-aware)
    - Checkpointing after each tool for crash recovery
    - Max iterations guard against infinite loops
    - Approval detection for dangerous operations

Tool Execution Pattern:
    LLM Response → Tool Calls → Execute (parallel) → Checkpoint → Repeat

Example:
    ```python
    # As child workflow (from session workflow)
    result = await workflow.execute_child_workflow(
        AgentTurnWorkflow.run,
        args=[session_id, turn_id, message, config],
        id=turn_id,
        execution_timeout=timedelta(minutes=5),
    )

    if result["needs_approval"]:
        # Handle approval flow
        ...
    ```
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    import logging

logger = logging.getLogger(__name__)


@dataclass
class TurnConfig:
    """Configuration for turn execution."""

    model_id: str = "gpt-4"
    max_iterations: int = 25
    checkpoint_interval: int = 1
    tools_enabled: list[str] = field(default_factory=list)
    llm_timeout_seconds: float = 120.0
    tool_timeout_seconds: float = 60.0
    approval_required_tools: list[str] = field(default_factory=list)


@dataclass
class ToolCall:
    """Parsed tool call from LLM response."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ToolResult:
    """Result of tool execution."""

    tool_call_id: str
    tool_name: str
    success: bool
    output: str
    error: str | None = None
    duration_seconds: float = 0.0


@dataclass
class TurnState:
    """Internal turn execution state."""

    iteration: int = 0
    messages: list[dict[str, Any]] = field(default_factory=list)
    tool_calls_total: int = 0
    tokens_used: int = 0
    needs_approval: bool = False
    approval_id: str | None = None
    approval_tool_name: str | None = None
    approval_tool_args: dict[str, Any] | None = None
    stop_reason: str = "unknown"
    output_message: str = ""
    error: str | None = None


@workflow.defn
class AgentTurnWorkflow:
    """
    Workflow for executing a single agent turn.

    Handles the LLM → Tool → Repeat loop with:
    - Configurable max iterations
    - Parallel tool execution
    - Checkpoint persistence
    - Approval detection
    - Error recovery
    """

    def __init__(self) -> None:
        """Initialize turn state."""
        self._state = TurnState()
        self._session_id: str = ""
        self._turn_id: str = ""
        self._config: TurnConfig | None = None

    @workflow.run
    async def run(
        self,
        session_id: str,
        turn_id: str,
        user_message: str,
        config_dict: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Execute a single agent turn.

        Args:
            session_id: Parent session ID.
            turn_id: Unique turn identifier.
            user_message: User's input message.
            config_dict: Turn configuration as dict.

        Returns:
            Turn result with output, tool counts, approval status, etc.
        """
        self._session_id = session_id
        self._turn_id = turn_id
        self._config = TurnConfig(**config_dict)

        start_time = workflow.now()
        workflow.logger.info(
            f"Starting turn: session={session_id}, turn={turn_id}, "
            f"message={user_message[:50]}..."
        )

        try:
            # Load context from persistent store
            context = await self._load_context()
            self._state.messages = context.get("messages", [])

            # Add user message
            self._state.messages.append({
                "role": "user",
                "content": user_message,
            })

            # Main execution loop
            await self._execution_loop()

        except Exception as e:
            workflow.logger.error(f"Turn failed: {e}")
            self._state.error = str(e)
            self._state.stop_reason = "error"

        # Calculate duration
        duration = (workflow.now() - start_time).total_seconds()

        # Persist final state
        if not self._state.needs_approval:
            await self._persist_turn()

        return self._build_result(duration)

    async def _load_context(self) -> dict[str, Any]:
        """Load conversation context from persistent store."""
        return await workflow.execute_activity(
            "load_context",
            args=[self._session_id],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(
                maximum_attempts=3,
                initial_interval=timedelta(seconds=1),
            ),
        )

    async def _execution_loop(self) -> None:
        """
        Main LLM → Tool execution loop.

        Continues until:
        - LLM returns no tool calls (natural end)
        - Max iterations reached
        - Approval required
        - Error occurs
        """
        assert self._config is not None

        while self._state.iteration < self._config.max_iterations:
            self._state.iteration += 1
            workflow.logger.info(
                f"Turn iteration {self._state.iteration}/{self._config.max_iterations}"
            )

            # Call LLM
            try:
                completion = await self._call_llm()
            except ActivityError as e:
                workflow.logger.error(f"LLM call failed: {e}")
                self._state.stop_reason = "llm_error"
                self._state.error = str(e)
                break

            # Extract tool calls
            tool_calls = self._parse_tool_calls(completion)
            assistant_message = completion.get("message", {})

            # Add assistant message to history
            self._state.messages.append(assistant_message)

            # Update token usage
            usage = completion.get("usage", {})
            self._state.tokens_used += usage.get("total_tokens", 0)

            # No tool calls = done
            if not tool_calls:
                self._state.output_message = assistant_message.get("content", "")
                self._state.stop_reason = "end_turn"
                workflow.logger.info("No tool calls, turn complete")
                break

            # Check for approval-required tools
            approval_needed = self._check_approval_required(tool_calls)
            if approval_needed:
                await self._request_approval(approval_needed)
                break

            # Execute tools in parallel
            tool_results = await self._execute_tools_parallel(tool_calls)

            # Add tool results to messages
            for result in tool_results:
                self._state.messages.append({
                    "role": "tool",
                    "tool_call_id": result.tool_call_id,
                    "content": result.output if result.success else f"Error: {result.error}",
                })

            # Checkpoint
            if self._state.iteration % self._config.checkpoint_interval == 0:
                await self._checkpoint()

        else:
            # Max iterations reached
            workflow.logger.warning(
                f"Max iterations reached: {self._config.max_iterations}"
            )
            self._state.stop_reason = "max_iterations"

    async def _call_llm(self) -> dict[str, Any]:
        """Call LLM via activity."""
        assert self._config is not None

        return await workflow.execute_activity(
            "call_llm",
            args=[
                self._session_id,
                self._state.messages,
                self._config.model_id,
                self._config.tools_enabled,
            ],
            start_to_close_timeout=timedelta(seconds=self._config.llm_timeout_seconds),
            retry_policy=RetryPolicy(
                maximum_attempts=3,
                initial_interval=timedelta(seconds=2),
                maximum_interval=timedelta(seconds=30),
                backoff_coefficient=2.0,
                non_retryable_error_types=["InvalidRequestError", "AuthenticationError"],
            ),
        )

    def _parse_tool_calls(self, completion: dict[str, Any]) -> list[ToolCall]:
        """Parse tool calls from LLM completion."""
        tool_calls_raw = completion.get("tool_calls", [])

        return [
            ToolCall(
                id=tc.get("id", ""),
                name=tc.get("function", {}).get("name", ""),
                arguments=tc.get("function", {}).get("arguments", {}),
            )
            for tc in tool_calls_raw
            if tc.get("type") == "function"
        ]

    def _check_approval_required(
        self,
        tool_calls: list[ToolCall],
    ) -> ToolCall | None:
        """Check if any tool call requires approval."""
        assert self._config is not None

        for tc in tool_calls:
            if tc.name in self._config.approval_required_tools:
                return tc
        return None

    async def _request_approval(self, tool_call: ToolCall) -> None:
        """Set up approval request for dangerous tool."""
        self._state.needs_approval = True
        self._state.approval_id = f"{self._turn_id}-approval-{tool_call.id}"
        self._state.approval_tool_name = tool_call.name
        self._state.approval_tool_args = tool_call.arguments
        self._state.stop_reason = "needs_approval"

        workflow.logger.info(
            f"Approval required for tool: {tool_call.name}, "
            f"approval_id={self._state.approval_id}"
        )

    async def _execute_tools_parallel(
        self,
        tool_calls: list[ToolCall],
    ) -> list[ToolResult]:
        """
        Execute multiple tools in parallel via activities.

        Each tool execution:
        - Has its own timeout
        - Heartbeats for long-running operations
        - Can be retried independently
        """
        assert self._config is not None

        workflow.logger.info(f"Executing {len(tool_calls)} tools in parallel")
        self._state.tool_calls_total += len(tool_calls)

        # Create activity tasks
        tasks = []
        for tc in tool_calls:
            task = workflow.execute_activity(
                "execute_tool",
                args=[
                    self._session_id,
                    self._turn_id,
                    tc.id,
                    tc.name,
                    tc.arguments,
                ],
                start_to_close_timeout=timedelta(
                    seconds=self._config.tool_timeout_seconds
                ),
                heartbeat_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(
                    maximum_attempts=2,
                    initial_interval=timedelta(seconds=1),
                    non_retryable_error_types=[
                        "ToolNotFoundError",
                        "InvalidArgumentsError",
                        "PermissionDeniedError",
                    ],
                ),
            )
            tasks.append((tc, task))

        # Gather results (don't fail fast - collect all)
        results = []
        for tc, task in tasks:
            try:
                result_dict = await task
                results.append(ToolResult(
                    tool_call_id=tc.id,
                    tool_name=tc.name,
                    success=result_dict.get("success", True),
                    output=result_dict.get("output", ""),
                    error=result_dict.get("error"),
                    duration_seconds=result_dict.get("duration_seconds", 0.0),
                ))
            except ActivityError as e:
                workflow.logger.warning(f"Tool {tc.name} failed: {e}")
                results.append(ToolResult(
                    tool_call_id=tc.id,
                    tool_name=tc.name,
                    success=False,
                    output="",
                    error=str(e),
                ))

        return results

    async def _checkpoint(self) -> None:
        """Checkpoint current state for crash recovery."""
        await workflow.execute_activity(
            "checkpoint",
            args=[
                self._session_id,
                self._turn_id,
                self._state.messages,
                self._state.iteration,
            ],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )

    async def _persist_turn(self) -> None:
        """Persist final turn state."""
        await workflow.execute_activity(
            "persist_turn",
            args=[
                self._session_id,
                self._turn_id,
                self._state.messages,
                {
                    "iterations": self._state.iteration,
                    "tool_calls_total": self._state.tool_calls_total,
                    "tokens_used": self._state.tokens_used,
                    "stop_reason": self._state.stop_reason,
                },
            ],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )

    def _build_result(self, duration_seconds: float) -> dict[str, Any]:
        """Build turn result dictionary."""
        result: dict[str, Any] = {
            "turn_id": self._turn_id,
            "session_id": self._session_id,
            "iterations": self._state.iteration,
            "tool_calls_count": self._state.tool_calls_total,
            "tokens_used": self._state.tokens_used,
            "stop_reason": self._state.stop_reason,
            "output": self._state.output_message,
            "duration_seconds": duration_seconds,
            "needs_approval": self._state.needs_approval,
        }

        if self._state.needs_approval:
            result["approval_id"] = self._state.approval_id
            result["approval_prompt"] = f"Approve execution of {self._state.approval_tool_name}?"
            result["approval_options"] = ["approve", "deny", "modify"]
            result["tool_name"] = self._state.approval_tool_name
            result["tool_args"] = self._state.approval_tool_args

        if self._state.error:
            result["error"] = self._state.error

        return result

    # ─────────────────────────────────────────────────────────────────────────
    # Queries - Read-only state inspection
    # ─────────────────────────────────────────────────────────────────────────

    @workflow.query
    def get_progress(self) -> dict[str, Any]:
        """Query turn execution progress."""
        return {
            "iteration": self._state.iteration,
            "tool_calls_total": self._state.tool_calls_total,
            "tokens_used": self._state.tokens_used,
            "needs_approval": self._state.needs_approval,
            "stop_reason": self._state.stop_reason,
        }

    @workflow.query
    def get_messages_count(self) -> int:
        """Query number of messages in context."""
        return len(self._state.messages)
