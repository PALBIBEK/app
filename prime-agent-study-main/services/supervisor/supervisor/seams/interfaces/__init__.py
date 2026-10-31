"""Protocol definitions (contracts) for all seams."""

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
    "CompactionDecision",
    "CompactionPreparation",
    "CompactionResult",
    # ContextMemoryProvider
    "ContextMemoryProvider",
    "MemoryInvokeContext",
    # ModelGatewayClient
    "ModelCatalog",
    "ModelGatewayClient",
    # PromptAssembler
    "PromptAssembler",
    "RecallBlock",
    # ToolProvider
    "ToolClassifier",
    "ToolProvider",
]
