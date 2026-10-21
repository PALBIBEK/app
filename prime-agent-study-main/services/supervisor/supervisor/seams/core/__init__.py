"""Core types, errors, and factory for seams."""

from supervisor.seams.core.errors import (
    BadArgs,
    DepthExceeded,
    ProviderConflict,
    ProviderMissing,
    SeamError,
    UnsupportedCapability,
)
from supervisor.seams.core.types import (
    AssembledPrompt,
    ChatMessage,
    ChildHandle,
    ChildInfo,
    Completion,
    JobHandle,
    PromptBindings,
    StreamEvent,
    SubagentRequest,
    SubagentRunResult,
    ToolDescriptor,
    ToolInvokeContext,
    ToolResult,
)

__all__ = [
    # Types
    "AssembledPrompt",
    # Errors
    "BadArgs",
    "ChatMessage",
    "ChildHandle",
    "ChildInfo",
    "Completion",
    "DepthExceeded",
    "JobHandle",
    "PromptBindings",
    "ProviderConflict",
    "ProviderMissing",
    "SeamError",
    "StreamEvent",
    "SubagentRequest",
    "SubagentRunResult",
    "ToolDescriptor",
    "ToolInvokeContext",
    "ToolResult",
    "UnsupportedCapability",
]
