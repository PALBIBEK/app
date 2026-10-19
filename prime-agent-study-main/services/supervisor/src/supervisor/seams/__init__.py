"""Capability seams: ToolProvider, PromptAssembler, SubagentTransport, ModelGatewayClient, ContextMemoryProvider.

Folder Structure:
- interfaces/  → Protocol definitions (contracts)
- core/        → Types, errors, factory
- providers/   → Concrete implementations (by provider type)
  - tools/     → ToolProvider implementations
  - gateway/   → ModelGatewayClient implementations
  - prompt/    → PromptAssembler implementations
  - subagent/  → SubagentTransport implementations
  - memory/    → ContextMemoryProvider implementations
- services/    → Additional service abstractions

Consumers import Protocols / DTOs / errors from this package. Concrete providers
live under ``supervisor.seams.providers`` and are constructed only via
``build_runtime_seams`` (or tests).

``build_runtime_seams`` is imported lazily to avoid a cycle with
``supervisor.agent.seams.RuntimeSeams``.
"""

from __future__ import annotations

from typing import Any

# Import from new locations (core/)
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

# Import from new locations (interfaces/)
from supervisor.seams.interfaces.context_memory import (
    CompactionDecision,
    CompactionPreparation,
    CompactionResult,
    ContextMemoryProvider,
    MemoryInvokeContext,
    RecallBlock,
)
from supervisor.seams.interfaces.model_gateway import ModelCatalog, ModelGatewayClient
from supervisor.seams.interfaces.prompt_assembler import PromptAssembler
from supervisor.seams.interfaces.subagent_transport import (
    NamedProviderRegistry,
    SubagentTransport,
    SubagentTransportImpl,
)
from supervisor.seams.interfaces.tool_provider import ToolClassifier, ToolProvider

__all__ = [
    # Types (from core/)
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
    # Errors (from core/)
    "BadArgs",
    "DepthExceeded",
    "ProviderConflict",
    "ProviderMissing",
    "SeamError",
    "UnsupportedCapability",
    # Interfaces (from interfaces/)
    "CompactionDecision",
    "CompactionPreparation",
    "CompactionResult",
    "ContextMemoryProvider",
    "MemoryInvokeContext",
    "ModelCatalog",
    "ModelGatewayClient",
    "NamedProviderRegistry",
    "PromptAssembler",
    "RecallBlock",
    "SubagentTransport",
    "SubagentTransportImpl",
    "ToolClassifier",
    "ToolProvider",
    # Factory (lazy loaded)
    "build_runtime_seams",
]


def __getattr__(name: str) -> Any:
    if name == "build_runtime_seams":
        from supervisor.seams.core.factory import build_runtime_seams

        return build_runtime_seams
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
