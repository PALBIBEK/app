"""PromptAssembler seam definition."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass
class PromptBindings:
    """Inputs for assembling a system prompt (pack pin comes in catalog-pins)."""

    cwd: str = "."
    tools: list[Any] | None = None
    skill_listing: str = ""
    extra: str = ""
    custom: str = ""


@dataclass
class AssembledPrompt:
    """Assembled system prompt text.

    `content_digest` is an optional content hash for test stability only —
    not a session catalog pin (deferred to catalog-pins).
    """

    text: str
    content_digest: str | None = None


@runtime_checkable
class PromptAssembler(Protocol):
    """Definition: turn bindings into assembled system prompt text."""

    def assemble(self, bindings: PromptBindings) -> AssembledPrompt:
        ...
