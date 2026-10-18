"""ToolProvider seam definition."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from ..tools.base import ToolContext, ToolResult


@dataclass(frozen=True)
class ToolDescriptor:
    """Wire-facing tool metadata (name + schema + short description)."""

    name: str
    description: str
    parameters: dict[str, Any]


@runtime_checkable
class ToolProvider(Protocol):
    """Definition: list and invoke tools without knowing the registry class."""

    def list_tools(self) -> list[ToolDescriptor]:
        """Return descriptors for tools currently offered."""
        ...

    def invoke(
        self,
        ctx: ToolContext,
        name: str,
        args: dict[str, Any],
    ) -> ToolResult:
        """Execute `name` with `args`; must not crash the agent process."""
        ...
