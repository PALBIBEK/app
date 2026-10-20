"""Tool failures, each carrying the machine-readable code a ``ToolResult`` reports.

A tool signals an expected failure (bad path, missing file, timeout) by raising
one of these. The seam turns it into ``ToolResult(ok=False, code=...)`` so the
model reads what went wrong and can correct itself on its next step; nothing
here is allowed to end a run.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import ClassVar

__all__ = [
    "FileMissing",
    "InvalidArguments",
    "PathEscape",
    "ToolError",
    "ToolNotFound",
    "ToolTimeout",
    "UnknownTools",
    "UnsupportedCapability",
    "WorkspaceReset",
    "WorkspaceUnavailable",
]


class ToolError(Exception):
    """An expected tool failure, reported to the model rather than raised to the run."""

    code: ClassVar[str] = "TOOL_ERROR"


class ToolNotFound(ToolError):
    code = "TOOL_NOT_FOUND"

    def __init__(self, name: str) -> None:
        super().__init__(f"tool {name!r} is not offered to this session")
        self.name = name


class UnknownTools(ToolError, LookupError):
    """A tool set names tools no implementation is registered for.

    Raised when binding a pinned tool set, so a session never starts with tools
    that would only fail once the model calls them.
    """

    code = "TOOL_NOT_IMPLEMENTED"

    def __init__(self, names: Iterable[str], *, registry: str) -> None:
        self.names = tuple(sorted(set(names)))
        super().__init__(
            f"no implementation in the {registry!r} tool registry for: {', '.join(self.names)}"
        )


class InvalidArguments(ToolError, ValueError):
    code = "BAD_ARGS"


class PathEscape(ToolError, PermissionError):
    code = "PATH_ESCAPE"

    def __init__(self, path: str) -> None:
        super().__init__(f"path {path!r} is outside the workspace")
        self.path = path


class FileMissing(ToolError, FileNotFoundError):
    code = "FILE_NOT_FOUND"

    def __init__(self, path: str, *, kind: str = "file") -> None:
        super().__init__(f"{kind} not found: {path}")
        self.path = path


class ToolTimeout(ToolError, TimeoutError):
    code = "TOOL_TIMEOUT"

    def __init__(self, name: str, seconds: float) -> None:
        super().__init__(f"tool {name!r} did not finish within {seconds:g}s")


class UnsupportedCapability(ToolError):
    """The session's workspace cannot do what the tool needs (e.g. run a process)."""

    code = "UNSUPPORTED_CAPABILITY"


class WorkspaceUnavailable(ToolError):
    """The invoke context names no workspace for the tool to act on."""

    code = "WORKSPACE_UNAVAILABLE"


class WorkspaceReset(ToolError):
    """The sandbox behind the workspace died; the next call gets a fresh one.

    The model reads this like any tool failure: the call did not run (or its
    outcome was lost with the sandbox), the session goes on, and what survives
    in the new sandbox is what was saved at the end of the last turn.
    """

    code = "WORKSPACE_RESET"

    def __init__(self, reason: str) -> None:
        super().__init__(
            "your workspace was reset: its sandbox stopped "
            f"({reason}). A fresh one starts with your next tool call, holding the "
            "files saved at the end of the last turn; running processes and changes "
            "made since then are gone. Check the files you need, then retry."
        )
        self.reason = reason
