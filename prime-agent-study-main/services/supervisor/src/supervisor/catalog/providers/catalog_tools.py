"""Catalog-backed ToolProvider — tools from a pinned ToolSet."""

from __future__ import annotations

from typing import Any

from supervisor.catalog.tools.handlers import get_handler, invoke_builtin
from supervisor.catalog.core.types import ToolSet
from supervisor.seams import ToolDescriptor, ToolInvokeContext, ToolResult


class CatalogToolProvider:
    """Provider: list/invoke tools from an embedded ToolSet definitions map."""

    def __init__(self, tool_set: ToolSet) -> None:
        self._tool_set = tool_set

    def list_tools(self) -> list[ToolDescriptor]:
        out: list[ToolDescriptor] = []
        for name in sorted(self._tool_set.tools.keys()):
            td = self._tool_set.definitions.get(name)
            if td is None:
                continue
            out.append(
                ToolDescriptor(
                    name=td.name,
                    description=td.description,
                    parameters=dict(td.parameters),
                )
            )
        return out

    def invoke(
        self,
        ctx: ToolInvokeContext,
        name: str,
        args: dict[str, Any],
    ) -> ToolResult:
        if name not in self._tool_set.tools:
            return ToolResult(
                ok=False,
                payload=None,
                error=f"tool {name!r} is not in pinned ToolSet {self._tool_set.name!r}",
                code="TOOL_NOT_FOUND",
            )
        if get_handler(name) is None:
            return ToolResult(
                ok=False,
                payload=None,
                error=(
                    f"tool {name!r} is in pinned ToolSet {self._tool_set.name!r} "
                    "but has no in-process handler"
                ),
                code="TOOL_NOT_IMPLEMENTED",
            )
        try:
            payload = invoke_builtin(name, args, ctx)
            return ToolResult(ok=True, payload=payload)
        except Exception as exc:
            return ToolResult(
                ok=False,
                payload=None,
                error=str(exc),
                code="TOOL_INVOKE_ERROR",
            )
