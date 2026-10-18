"""Protocol definitions (contracts) for all seams."""

from supervisor.seams.interfaces.tool_provider import ToolClassifier, ToolProvider
from supervisor.seams.interfaces.model_gateway import ModelGatewayClient
from supervisor.seams.interfaces.prompt_assembler import PromptAssembler
from supervisor.seams.interfaces.subagent_transport import (
    SubagentTransport,
    SubagentTransportImpl,
    NamedProviderRegistry,
)
from supervisor.seams.interfaces.context_memory import (
    ContextMemoryProvider,
    CompactionDecision,
    CompactionPreparation,
    CompactionResult,
    MemoryInvokeContext,
    RecallBlock,
)

__all__ = [
    # ToolProvider
    "ToolClassifier",
    "ToolProvider",
    # ModelGatewayClient
    "ModelGatewayClient",
    # PromptAssembler
    "PromptAssembler",
    # SubagentTransport
    "SubagentTransport",
    "SubagentTransportImpl",
    "NamedProviderRegistry",
    # ContextMemoryProvider
    "ContextMemoryProvider",
    "CompactionDecision",
    "CompactionPreparation",
    "CompactionResult",
    "MemoryInvokeContext",
    "RecallBlock",
]
