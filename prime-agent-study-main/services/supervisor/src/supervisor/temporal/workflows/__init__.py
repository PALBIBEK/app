"""
Temporal workflow definitions for agent orchestration.

Workflow Hierarchy:
    AgentSessionWorkflow (long-lived, per session)
        └── AgentTurnWorkflow (bounded, per user turn)

Design Principles:
    1. Workflows are deterministic - no I/O, randomness, or wall clock
    2. All external interactions via Activities
    3. State persisted in workflow variables (survives replay)
    4. Signals for external events (messages, approvals, stop)
    5. Queries for read-only state inspection

Session Lifecycle:
    1. Workflow started on session creation
    2. Waits for messages via signal
    3. Spawns child workflow for each turn
    4. Pauses for human approval if needed
    5. Continues until stop signal or session timeout

Turn Lifecycle:
    1. Load context from persistent store
    2. Call LLM with tools
    3. Execute tools (with checkpointing)
    4. Repeat until no more tool calls
    5. Persist final state
"""

from supervisor.temporal.workflows.session import AgentSessionWorkflow
from supervisor.temporal.workflows.turn import AgentTurnWorkflow

__all__ = [
    "AgentSessionWorkflow",
    "AgentTurnWorkflow",
]
