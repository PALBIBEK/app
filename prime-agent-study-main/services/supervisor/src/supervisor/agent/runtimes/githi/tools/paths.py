"""Workspace path confinement for githi file tools.

User-supplied paths must resolve under the session workspace root.
Absolute paths outside the root and ``..`` escapes are rejected.
"""

from __future__ import annotations

from pathlib import Path

from supervisor.agent.runtimes.githi.tools.base import ToolError


class PathEscapeError(ToolError):
    """Path resolves outside the workspace root."""


def resolve_in_workspace(workspace_root: str | Path, user_path: str) -> Path:
    """Resolve ``user_path`` under ``workspace_root`` or raise ``PathEscapeError``."""
    if user_path is None or user_path == "":
        raise ToolError("path must be a non-empty string")

    root = Path(workspace_root).expanduser().resolve()
    raw = Path(user_path).expanduser()
    candidate = (root / raw).resolve() if not raw.is_absolute() else raw.resolve()

    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise PathEscapeError(
            f"path {user_path!r} escapes workspace root {str(root)!r}"
        ) from exc
    return candidate
