"""
Temporal integration for durable agent workflows.

This module provides Temporal-based orchestration for agent sessions,
replacing in-process state with durable, fault-tolerant workflows.

Architecture:
    ┌─────────────────────────────────────────────────────────────┐
    │  AgentSessionWorkflow (per session, long-lived)             │
    │  - Survives process restarts, deployments, human waits      │
    │  - Maintains session state via workflow variables           │
    │  - Handles signals: send_message, approve, stop             │
    └─────────────────────────────────────────────────────────────┘
                              │
    ┌─────────────────────────────────────────────────────────────┐
    │  AgentTurnWorkflow (per turn, child workflow)               │
    │  - Executes single user turn with tool loop                 │
    │  - Max iterations guard against infinite loops              │
    │  - Checkpoints after each tool execution                    │
    └─────────────────────────────────────────────────────────────┘
                              │
    ┌─────────────────────────────────────────────────────────────┐
    │  Activities                                                  │
    │  - LLM calls (retriable, with timeout)                      │
    │  - Tool execution (heartbeat for long-running)              │
    │  - Checkpoint persistence                                    │
    │  - Context loading/saving                                    │
    └─────────────────────────────────────────────────────────────┘

Usage:
    from supervisor.temporal import (
        TemporalClient,
        create_temporal_client,
        create_worker,
        AgentSessionWorkflow,
        AgentTurnWorkflow,
    )

    # Connect to Temporal
    client = await create_temporal_client("localhost:7233")

    # Start session workflow
    handle = await client.start_workflow(
        AgentSessionWorkflow.run,
        args=[session_id, config],
        id=f"session-{session_id}",
        task_queue="agent-sessions",
    )

    # Send message via signal
    await handle.signal(AgentSessionWorkflow.send_message, "Hello")

Design Principles:
- Workflows are deterministic (no I/O, random, or time.time())
- Activities handle all external interactions
- Child workflows for bounded operations (turns)
- Signals for external events (messages, approvals)
- Queries for read-only state inspection
"""

from supervisor.temporal.client import (
    TemporalClient,
    TemporalConfig,
    create_temporal_client,
    create_worker,
    WorkerConfig,
)

__all__ = [
    # Client
    "TemporalClient",
    "TemporalConfig",
    "create_temporal_client",
    "create_worker",
    "WorkerConfig",
]
