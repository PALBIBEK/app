"""
Base tool class for Githi runtime.

All tools inherit from Tool base class and implement execute() method.

Pattern: Base class with template method (PYTHON_PATTERNS_MASTERCLASS.md Section 4.1)
"""

from __future__ import annotations

import typing as t
from dataclasses import dataclass
from abc import ABC, abstractmethod

if t.TYPE_CHECKING:
    from supervisor.seams.services import WorkspaceTreeIO


class ToolError(Exception):
    """Base exception for tool execution errors"""

    pass


@dataclass(frozen=True)
class ToolExecutionContext:
    """
    Context passed to tool execution.

    Provides tool with access to session state and utilities.

    Pattern: Immutable context object (PYTHON_PATTERNS_MASTERCLASS.md)
    """

    session_id: str
    working_directory: str = "."
    timeout: int = 120
    sandbox_enabled: bool = False
    tree: "WorkspaceTreeIO | None" = None
    on_mutate: t.Callable[[], None] | None = None

    def require_tree(self) -> "WorkspaceTreeIO":
        if self.sandbox_enabled and self.tree is None:
            raise ToolError("sandbox workspace TreeIO required but missing")
        if self.tree is None:
            raise ToolError("workspace TreeIO not configured")
        return self.tree


class Tool(ABC):
    """
    Base class for all tools.

    All tools must:
    1. Define name (class attribute)
    2. Define schema (method)
    3. Implement execute (async method)
    """

    name: str

    @abstractmethod
    def get_schema(self) -> dict[str, t.Any]:
        pass

    @abstractmethod
    async def execute(
        self,
        args: dict[str, t.Any],
        context: ToolExecutionContext,
    ) -> t.Any:
        pass

    def validate_args(self, args: dict[str, t.Any]) -> tuple[bool, str | None]:
        schema = self.get_schema()
        input_schema = schema.get("input_schema", {})
        required = input_schema.get("required", [])

        for field in required:
            if field not in args:
                return False, f"Missing required field: {field}"

        return True, None

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r})"
