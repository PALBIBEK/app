"""ToolProvider seam definition."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from supervisor.seams.core.types import ToolDescriptor, ToolInvokeContext, ToolResult

__all__ = ["ToolClassifier", "ToolDescriptor", "ToolInvokeContext", "ToolProvider", "ToolResult"]


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


@runtime_checkable
class ToolClassifier(Protocol):
    """Optional: a provider that knows each tool's effect class (``pure``, ``idempotent`` …).

    Policy (a sandbox profile's ``file_effects``) and recovery key on it; a
    provider that cannot say leaves policy to its name-based defaults.
    """

    def tool_class(self, name: str) -> str | None: ...
