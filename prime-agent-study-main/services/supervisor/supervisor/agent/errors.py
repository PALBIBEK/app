"""Agent error hierarchy with error codes, retry flags, and HTTP status mapping."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

_BASE_FIELDS = frozenset({"message", "session_id", "error_code", "retryable", "context"})


def _auto_context[E: type](cls: E) -> E:
    """Decorator: auto-populate context dict from subclass-specific fields."""
    klass: Any = cls
    orig = klass.__post_init__

    def __post_init__(self: Any) -> None:
        for f in self.__dataclass_fields__:
            if f not in _BASE_FIELDS and (v := getattr(self, f)) is not None:
                self.context[f] = v
        orig(self)

    klass.__post_init__ = __post_init__
    return cls


@dataclass
class AgentError(Exception):
    """Base exception for agent operations."""
    message: str
    session_id: str | None = None
    error_code: str = "AGENT_ERROR"
    retryable: bool = False
    context: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        super().__init__(str(self))

    def __str__(self) -> str:
        base = f"[{self.error_code}] {self.message}"
        return f"{base} (session={self.session_id})" if self.session_id else base

    @property
    def http_status(self) -> int:
        return 500

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": self.error_code,
            "message": self.message,
            "session_id": self.session_id,
            "retryable": self.retryable,
            "context": self.context,
        }


@dataclass
class AgentNotReadyError(AgentError):
    """Agent not ready (not started/initializing). HTTP 503."""
    message: str = "Agent not ready"
    error_code: str = "AGENT_NOT_READY"
    retryable: bool = True

    @property
    def http_status(self) -> int:
        return 503


@_auto_context
@dataclass
class AgentBusyError(AgentError):
    """Agent already executing. HTTP 429."""
    message: str = "Agent is busy"
    error_code: str = "AGENT_BUSY"
    retryable: bool = True
    current_operation: str | None = None

    @property
    def http_status(self) -> int:
        return 429


@_auto_context
@dataclass
class AgentAbortedError(AgentError):
    """Execution aborted. HTTP 499."""
    message: str = "Execution aborted"
    error_code: str = "AGENT_ABORTED"
    abort_reason: str = "user_request"

    @property
    def http_status(self) -> int:
        return 499


@_auto_context
@dataclass
class AgentTimeoutError(AgentError):
    """Operation timed out. HTTP 504."""
    message: str = "Operation timed out"
    error_code: str = "AGENT_TIMEOUT"
    retryable: bool = True
    timeout_seconds: float = 0.0

    @property
    def http_status(self) -> int:
        return 504


@_auto_context
@dataclass
class ToolExecutionError(AgentError):
    """Tool execution failed."""
    message: str = "Tool execution failed"
    error_code: str = "TOOL_EXECUTION_ERROR"
    tool_name: str | None = None
    tool_call_id: str | None = None
    exit_code: int | None = None


@_auto_context
@dataclass
class ToolNotFoundError(AgentError):
    """Tool not found. HTTP 404."""
    message: str = "Tool not found"
    error_code: str = "TOOL_NOT_FOUND"
    tool_name: str | None = None

    def __post_init__(self) -> None:
        if self.tool_name:
            self.message = f"Tool not found: {self.tool_name}"
        super().__post_init__()

    @property
    def http_status(self) -> int:
        return 404


@_auto_context
@dataclass
class ModelCallError(AgentError):
    """LLM call failed."""
    message: str = "Model call failed"
    error_code: str = "MODEL_CALL_ERROR"
    retryable: bool = True
    model_id: str | None = None
    status_code: int | None = None
    provider: str | None = None


@_auto_context
@dataclass
class ModelOverloadedError(ModelCallError):
    """Model rate-limited. HTTP 429."""
    message: str = "Model overloaded"
    error_code: str = "MODEL_OVERLOADED"
    retry_after: float | None = None

    @property
    def http_status(self) -> int:
        return 429


@_auto_context
@dataclass
class ContextOverflowError(AgentError):
    """Context window exceeded."""
    message: str = "Context window exceeded"
    error_code: str = "CONTEXT_OVERFLOW"
    tokens_used: int = 0
    tokens_limit: int = 0


@_auto_context
@dataclass
class LoopLimitError(AgentError):
    """Tool loop limit exceeded."""
    message: str = "Tool loop limit exceeded"
    error_code: str = "LOOP_LIMIT"
    iterations: int = 0
    max_iterations: int = 0


@_auto_context
@dataclass
class CompactionError(AgentError):
    """Context compaction failed."""
    message: str = "Context compaction failed"
    error_code: str = "COMPACTION_ERROR"
    retryable: bool = True
    compaction_id: str | None = None


@_auto_context
@dataclass
class CapabilityError(AgentError):
    """Required capability unavailable."""
    message: str = "Capability not available"
    error_code: str = "CAPABILITY_ERROR"
    required: str | None = None
    available: list[str] = field(default_factory=list)


@_auto_context
@dataclass
class ProtocolError(AgentError):
    """Protocol violation."""
    message: str = "Protocol violation"
    error_code: str = "PROTOCOL_ERROR"
    expected: str | None = None
    received: str | None = None


@_auto_context
@dataclass
class SandboxError(AgentError):
    """Sandbox operation failed."""
    message: str = "Sandbox error"
    error_code: str = "SANDBOX_ERROR"
    retryable: bool = True
    sandbox_id: str | None = None


@_auto_context
@dataclass
class ApprovalError(AgentError):
    """Approval flow error."""
    message: str = "Approval error"
    error_code: str = "APPROVAL_ERROR"
    approval_id: str | None = None
    status: str | None = None


@_auto_context
@dataclass
class ApprovalTimeoutError(ApprovalError):
    """Approval timed out."""
    message: str = "Approval timed out"
    error_code: str = "APPROVAL_TIMEOUT"
    timeout_seconds: float = 0.0


# Legacy aliases
AgentProtocolError = ProtocolError
AgentGone = AgentNotReadyError
AgentCommandError = ToolExecutionError
