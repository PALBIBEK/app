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
    ChildId,
    ChildInfo,
    Completion,
    JobHandle,
    JobId,
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
    "ChatMessage",
    "ChildHandle",
    "ChildId",
    "ChildInfo",
    "Completion",
    "JobHandle",
    "JobId",
    "PromptBindings",
    "StreamEvent",
    "SubagentRequest",
    "SubagentRunResult",
    "ToolDescriptor",
    "ToolInvokeContext",
    "ToolResult",
    # Errors
    "BadArgs",
    "DepthExceeded",
    "ProviderConflict",
    "ProviderMissing",
    "SeamError",
    "UnsupportedCapability",
]
