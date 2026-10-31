"""Core types, errors, and factory for seams."""

from supervisor.seams.core.errors import (
    BadArgs,
    ProviderConflict,
    ProviderMissing,
    SeamError,
    UnsupportedCapability,
)
from supervisor.seams.core.types import (
    AssembledPrompt,
    ChatMessage,
    Completion,
    PromptBindings,
    StreamEvent,
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
    "Completion",
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
