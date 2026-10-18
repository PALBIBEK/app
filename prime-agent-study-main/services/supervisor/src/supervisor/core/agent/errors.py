"""
Agent error hierarchy.

Typed exceptions for agent runtime operations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentError(Exception):
    """Base exception for agent operations."""

    message: str
    session_id: str | None = None
    error_code: str = "AGENT_ERROR"
    retryable: bool = False
    context: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        if self.session_id:
            return f"[{self.error_code}] {self.message} (session={self.session_id})"
        return f"[{self.error_code}] {self.message}"

    def __post_init__(self) -> None:
        super().__init__(str(self))


@dataclass
class AgentNotReadyError(AgentError):
    """Agent is not ready to execute."""

    message: str = "Agent not ready"
    error_code: str = "AGENT_NOT_READY"
    retryable: bool = True


@dataclass
class AgentBusyError(AgentError):
    """Agent is already executing."""

    message: str = "Agent is busy"
    error_code: str = "AGENT_BUSY"
    retryable: bool = True
    current_operation: str | None = None

    def __post_init__(self) -> None:
        if self.current_operation:
            self.context["current_operation"] = self.current_operation
        super().__post_init__()


@dataclass
class AgentAbortedError(AgentError):
    """Agent execution was aborted."""

    message: str = "Execution aborted"
    error_code: str = "AGENT_ABORTED"
    retryable: bool = False
    abort_reason: str = "user_request"

    def __post_init__(self) -> None:
        self.context["abort_reason"] = self.abort_reason
        super().__post_init__()


@dataclass
class ToolExecutionError(AgentError):
    """Tool execution failed."""

    message: str = "Tool execution failed"
    error_code: str = "TOOL_EXECUTION_ERROR"
    retryable: bool = False
    tool_name: str | None = None
    tool_call_id: str | None = None

    def __post_init__(self) -> None:
        if self.tool_name:
            self.context["tool_name"] = self.tool_name
        if self.tool_call_id:
            self.context["tool_call_id"] = self.tool_call_id
        super().__post_init__()


@dataclass
class ModelCallError(AgentError):
    """Model/LLM call failed."""

    message: str = "Model call failed"
    error_code: str = "MODEL_CALL_ERROR"
    retryable: bool = True
    model_id: str | None = None
    status_code: int | None = None

    def __post_init__(self) -> None:
        if self.model_id:
            self.context["model_id"] = self.model_id
        if self.status_code:
            self.context["status_code"] = self.status_code
        super().__post_init__()


@dataclass
class LeaseError(AgentError):
    """Lease acquisition/renewal failed."""

    message: str = "Lease operation failed"
    error_code: str = "LEASE_ERROR"
    retryable: bool = True
    lease_id: str | None = None

    def __post_init__(self) -> None:
        if self.lease_id:
            self.context["lease_id"] = self.lease_id
        super().__post_init__()


@dataclass
class LoopLimitError(AgentError):
    """Tool loop iteration limit exceeded."""

    message: str = "Tool loop limit exceeded"
    error_code: str = "LOOP_LIMIT"
    retryable: bool = False
    iterations: int = 0
    max_iterations: int = 0

    def __post_init__(self) -> None:
        self.context["iterations"] = self.iterations
        self.context["max_iterations"] = self.max_iterations
        super().__post_init__()
