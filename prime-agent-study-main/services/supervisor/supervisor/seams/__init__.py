"""Capability seams: ToolProvider, PromptAssembler, ModelGatewayClient, ContextMemoryProvider.

Folder Structure:
- interfaces/  → Protocol definitions (contracts)
- core/        → Types, errors, factory
- providers/   → Concrete implementations (by provider type)
  - tools/     → ToolProvider implementations
  - gateway/   → ModelGatewayClient implementations
  - prompt/    → PromptAssembler implementations
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
from supervisor.seams.interfaces.tool_provider import ToolClassifier, ToolProvider

__all__ = [
    # Types (from core/)
    "AssembledPrompt",
    # Errors (from core/)
    "BadArgs",
    "ChatMessage",
    # Interfaces (from interfaces/)
    "CompactionDecision",
    "CompactionPreparation",
    "CompactionResult",
    "Completion",
    "ContextMemoryProvider",
    "MemoryInvokeContext",
    "ModelCatalog",
    "ModelGatewayClient",
    "PromptAssembler",
    "PromptBindings",
    "ProviderConflict",
    "ProviderMissing",
    "RecallBlock",
    "SeamError",
    "StreamEvent",
    "ToolClassifier",
    "ToolDescriptor",
    "ToolInvokeContext",
    "ToolProvider",
    "ToolResult",
    "UnsupportedCapability",
    # Factory (lazy loaded)
    "build_runtime_seams",
]


def __getattr__(name: str) -> Any:
    if name == "build_runtime_seams":
        from supervisor.seams.core.factory import build_runtime_seams

        return build_runtime_seams
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
