"""PromptAssembler seam definition."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from supervisor.seams.core.types import AssembledPrompt, PromptBindings

__all__ = ["AssembledPrompt", "PromptAssembler", "PromptBindings"]


@runtime_checkable
class PromptAssembler(Protocol):
    """Definition: turn bindings into assembled system prompt text."""

    def assemble(self, bindings: PromptBindings) -> AssembledPrompt:
        ...
