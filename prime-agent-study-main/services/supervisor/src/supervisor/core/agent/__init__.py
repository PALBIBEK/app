"""
Agent runtime module.

Provides clean agent runtime implementations with:
- Protocol-based abstractions
- Proper lifecycle management
- Lease-based concurrency control
- Event streaming
- Error handling

Components:
    - AgentRuntime: Protocol for runtime implementations
    - GithiRuntime: LLM-powered agent runtime
    - AgentLoop: Tool execution loop
    - RuntimeManager: Runtime lifecycle management
"""

from supervisor.core.agent.protocol import (
    AgentRuntime,
    AgentState,
    AgentCapabilities,
    RuntimeEvent,
)
from supervisor.core.agent.errors import (
    AgentError,
    AgentNotReadyError,
    AgentBusyError,
    AgentAbortedError,
    ToolExecutionError,
    ModelCallError,
)

__all__ = [
    # Protocol
    "AgentRuntime",
    "AgentState",
    "AgentCapabilities",
    "RuntimeEvent",
    # Errors
    "AgentError",
    "AgentNotReadyError",
    "AgentBusyError",
    "AgentAbortedError",
    "ToolExecutionError",
    "ModelCallError",
]
