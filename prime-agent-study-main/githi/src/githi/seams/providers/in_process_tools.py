"""In-process ToolProvider over ToolRegistry."""

from __future__ import annotations

from typing import Any

from ...tools.base import ToolContext, ToolResult
from ...tools.registry import ToolRegistry
from ..tool_provider import ToolDescriptor


class InProcessToolProvider:
    """Provider: adapt an existing ToolRegistry to ToolProvider."""

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    def list_tools(self) -> list[ToolDescriptor]:
        out: list[ToolDescriptor] = []
        for name in self._registry.names():
            tool = self._registry.get(name)
            out.append(
                ToolDescriptor(
                    name=tool.name,
                    description=tool.description,
                    parameters=dict(tool.parameters or {}),
                )
            )
        return out

    def invoke(
        self,
        ctx: ToolContext,
        name: str,
        args: dict[str, Any],
    ) -> ToolResult:
        return self._registry.execute(name, args, ctx)
