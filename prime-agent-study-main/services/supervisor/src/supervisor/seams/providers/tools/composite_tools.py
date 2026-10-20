"""Composite ToolProvider — merge multiple providers (factory-only)."""

from __future__ import annotations

from typing import Any

from supervisor.seams.core.types import ToolDescriptor, ToolInvokeContext, ToolResult
from supervisor.seams.interfaces.tool_provider import (
    ToolClassifier,
    ToolProvider,
    needs_workspace,
)


class CompositeToolProvider:
    """Provider: ordered delegation for list_tools / invoke (first match wins)."""

    def __init__(self, *providers: ToolProvider) -> None:
        if not providers:
            raise ValueError("CompositeToolProvider requires at least one provider")
        self._providers = providers

    def list_tools(self) -> list[ToolDescriptor]:
        seen: set[str] = set()
        out: list[ToolDescriptor] = []
        for provider in self._providers:
            for tool in provider.list_tools():
                if tool.name in seen:
                    continue
                seen.add(tool.name)
                out.append(tool)
        return out

    def tool_class(self, name: str) -> str | None:
        """The class reported by the first provider that offers ``name`` and can classify."""
        for provider in self._providers:
            if name in {t.name for t in provider.list_tools()}:
                return provider.tool_class(name) if isinstance(provider, ToolClassifier) else None
        return None

    def needs_workspace(self, name: str) -> bool:
        """Whether the first provider offering ``name`` needs the session's sandbox for it."""
        for provider in self._providers:
            if name in {t.name for t in provider.list_tools()}:
                return needs_workspace(provider, name)
        return False

    def invoke(
        self,
        ctx: ToolInvokeContext,
        name: str,
        args: dict[str, Any],
    ) -> ToolResult:
        for provider in self._providers:
            names = {t.name for t in provider.list_tools()}
            if name in names:
                return provider.invoke(ctx, name, args)
        return ToolResult(
            ok=False,
            payload=None,
            error=f"tool {name!r} is not registered",
            code="TOOL_NOT_FOUND",
        )
