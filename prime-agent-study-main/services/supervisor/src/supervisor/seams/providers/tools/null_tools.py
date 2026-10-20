"""Null ToolProvider — empty catalog; unknown tools fail loud."""

from __future__ import annotations

from typing import Any

from supervisor.seams.core.types import ToolDescriptor, ToolInvokeContext, ToolResult


class NullToolProvider:
    """Provider: empty tool list; invoke returns typed not-found (no crash)."""

    def list_tools(self) -> list[ToolDescriptor]:
        return []

    def invoke(
        self,
        ctx: ToolInvokeContext,
        name: str,
        args: dict[str, Any],
    ) -> ToolResult:
        return ToolResult(
            ok=False,
            payload=None,
            error=f"tool {name!r} is not registered",
            code="TOOL_NOT_FOUND",
        )
