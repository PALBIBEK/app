"""ToolProvider seam definition."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from supervisor.seams.core.types import ToolDescriptor, ToolInvokeContext, ToolResult

__all__ = ["ToolDescriptor", "ToolInvokeContext", "ToolProvider", "ToolResult"]


@runtime_checkable
class ToolProvider(Protocol):
    """Definition: list and invoke tools without knowing the registry class."""

    def list_tools(self) -> list[ToolDescriptor]:
        """Return descriptors for tools currently offered."""
        ...

    def invoke(
        self,
        ctx: ToolInvokeContext,
        name: str,
        args: dict[str, Any],
    ) -> ToolResult:
        """Execute ``name`` with ``args``; must not crash the agent process."""
        ...
