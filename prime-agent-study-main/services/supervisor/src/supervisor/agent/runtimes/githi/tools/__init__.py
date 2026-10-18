"""
Githi tool system.

Tool execution infrastructure for Githi runtime:
- Base tool class
- Path confinement helpers
- Tool registry (builtin read/write/ls)
- Tool provider (implements ToolProvider protocol)
"""

from .base import Tool, ToolExecutionContext, ToolError
from .paths import PathEscapeError, resolve_in_workspace
from .provider import GithiToolProvider
from .registry import ToolRegistry, get_registry, reset_registry_for_tests

__all__ = [
    "Tool",
    "ToolExecutionContext",
    "ToolError",
    "PathEscapeError",
    "resolve_in_workspace",
    "ToolRegistry",
    "get_registry",
    "reset_registry_for_tests",
    "GithiToolProvider",
]
