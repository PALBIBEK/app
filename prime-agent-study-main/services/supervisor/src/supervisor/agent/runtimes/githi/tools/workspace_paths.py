"""Workspace-relative path helpers for TreeIO-backed tools (WP-M5)."""

from __future__ import annotations

from supervisor.agent.runtimes.githi.tools.base import ToolError


class PathEscapeError(ToolError):
    """Path resolves outside the workspace root."""


def workspace_relpath(user_path: str, *, default: str | None = None) -> str:
    """Normalize a user path to a relative posix path under ``/workspace``.

    Rejects empty paths, ``..`` segments, and absolute paths that are not
    under ``/workspace``. Does not touch the host filesystem (defense-in-depth
    for sandbox TreeIO).
    """
    raw = user_path if user_path is not None else ""
    if raw == "" and default is not None:
        raw = default
    if raw is None or raw == "":
        raise ToolError("path must be a non-empty string")

    text = str(raw).strip().replace("\\", "/")
    if "\x00" in text:
        raise PathEscapeError(f"path {user_path!r} contains NUL")

    if text.startswith("/"):
        if text == "/workspace" or text.startswith("/workspace/"):
            text = text[len("/workspace") :].lstrip("/")
        else:
            raise PathEscapeError(
                f"path {user_path!r} escapes workspace root '/workspace'"
            )

    parts: list[str] = []
    for part in text.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise PathEscapeError(
                f"path {user_path!r} escapes workspace root '/workspace'"
            )
        parts.append(part)
    return "/".join(parts)
