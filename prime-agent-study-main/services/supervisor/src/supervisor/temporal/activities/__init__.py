"""
Temporal activities for agent operations.

Activities handle all I/O operations - they are the only code that can
make external calls, access databases, call APIs, etc. Workflows delegate
all non-deterministic work to activities.

Activity Categories:
    1. Session: initialize, load context, persist state
    2. LLM: model calls with retry and timeout
    3. Tool: tool execution with heartbeats
    4. Checkpoint: durable state persistence
    5. Approval: approval request persistence

Design Principles:
    - Activities are idempotent (safe to retry)
    - Long-running activities send heartbeats
    - Retryable errors vs non-retryable errors
    - Structured logging with correlation IDs

Heartbeat Pattern:
    For long-running operations (tool execution, LLM calls), activities
    periodically call activity.heartbeat() to signal liveness. If heartbeats
    stop, Temporal knows the activity worker crashed and can retry.

Example:
    ```python
    @activity.defn
    async def execute_tool(
        session_id: str,
        turn_id: str,
        tool_call_id: str,
        tool_name: str,
        arguments: dict,
    ) -> dict:
        # Start heartbeat loop
        heartbeat_task = asyncio.create_task(heartbeat_loop())

        try:
            sandbox = get_sandbox(session_id)
            result = await sandbox.exec_tool(tool_name, arguments)
            return {"success": True, "output": result}
        finally:
            heartbeat_task.cancel()
    ```
"""

from supervisor.temporal.activities.session import (
    initialize_session,
    load_context,
    persist_turn,
    checkpoint,
    persist_approval_request,
    resume_turn_with_approval,
)
from supervisor.temporal.activities.llm import call_llm
from supervisor.temporal.activities.tools import execute_tool

# All activities for worker registration
ALL_ACTIVITIES = [
    initialize_session,
    load_context,
    persist_turn,
    checkpoint,
    persist_approval_request,
    resume_turn_with_approval,
    call_llm,
    execute_tool,
]

__all__ = [
    # Session activities
    "initialize_session",
    "load_context",
    "persist_turn",
    "checkpoint",
    "persist_approval_request",
    "resume_turn_with_approval",
    # LLM activities
    "call_llm",
    # Tool activities
    "execute_tool",
    # Collection
    "ALL_ACTIVITIES",
]
