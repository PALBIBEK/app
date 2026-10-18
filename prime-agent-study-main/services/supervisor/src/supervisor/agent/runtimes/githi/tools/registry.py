"""Tool registry for Githi runtime — singleton + builtin registration."""

from __future__ import annotations

import typing as t

if t.TYPE_CHECKING:
    from supervisor.agent.runtimes.githi.tools.base import Tool


class ToolRegistry:
    """Name → Tool instance map."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool {tool.name!r} already registered")
        self._tools[tool.name] = tool

    def get_tool(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def get_all_tools(self) -> dict[str, Tool]:
        return self._tools.copy()

    def get_all_schemas(self) -> list[dict[str, t.Any]]:
        return [tool.get_schema() for tool in self._tools.values()]

    def list_tool_names(self) -> list[str]:
        return list(self._tools.keys())

    def clear(self) -> None:
        self._tools.clear()

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools


_global_registry: ToolRegistry | None = None


def reset_registry_for_tests() -> None:
    """Drop the singleton so the next ``get_registry`` re-registers builtins."""
    global _global_registry
    _global_registry = None


def get_registry() -> ToolRegistry:
    global _global_registry
    if _global_registry is None:
        _global_registry = ToolRegistry()
        _register_builtin_tools(_global_registry)
    return _global_registry


def _register_builtin_tools(registry: ToolRegistry) -> None:
    """Register Phase-1 file tools (read / write / ls / grep)."""
    from supervisor.agent.runtimes.githi.tools.file.grep import GrepTool
    from supervisor.agent.runtimes.githi.tools.file.ls import LsTool
    from supervisor.agent.runtimes.githi.tools.file.read import ReadTool
    from supervisor.agent.runtimes.githi.tools.file.write import WriteTool

    registry.register(ReadTool())
    registry.register(WriteTool())
    registry.register(LsTool())
    registry.register(GrepTool())
