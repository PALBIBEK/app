"""Dependency injection seams for agent runtimes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from supervisor.seams import (
    ModelGatewayClient,
    PromptAssembler,
    SubagentTransport,
    ToolProvider,
)

if TYPE_CHECKING:
    from supervisor.seams import ContextMemoryProvider


_SEAM_FIELDS = ("tool_provider", "prompt_assembler", "subagent_transport", "model_gateway", "context_memory")


@dataclass(frozen=True)
class RuntimeSeams:
    """Immutable DI container for runtime services. All fields optional."""
    tool_provider: ToolProvider | None = None
    prompt_assembler: PromptAssembler | None = None
    subagent_transport: SubagentTransport | None = None
    model_gateway: ModelGatewayClient | None = None
    context_memory: ContextMemoryProvider | None = None

    def has(self, seam_name: str) -> bool:
        return getattr(self, seam_name) is not None

    def available_seams(self) -> list[str]:
        return [name for name in _SEAM_FIELDS if getattr(self, name) is not None]

    def to_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) is not None for name in _SEAM_FIELDS}


EMPTY_SEAMS = RuntimeSeams()
