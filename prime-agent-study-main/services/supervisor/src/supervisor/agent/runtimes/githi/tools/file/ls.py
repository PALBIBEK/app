"""List directory tool — TreeIO / path-confined (WP-M5)."""

from __future__ import annotations

import typing as t
from datetime import datetime

from supervisor.agent.runtimes.githi.tools.base import (
    Tool,
    ToolError,
    ToolExecutionContext,
)
from supervisor.agent.runtimes.githi.tools.paths import resolve_in_workspace
from supervisor.agent.runtimes.githi.tools.workspace_paths import workspace_relpath


class LsTool(Tool):
    """List directory contents under the session workspace."""

    name = "ls"

    def get_schema(self) -> dict[str, t.Any]:
        return {
            "name": "ls",
            "description": (
                "List directory contents in the workspace "
                "(size, modified time, type)."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory path (default: workspace root)",
                    },
                    "show_hidden": {
                        "type": "boolean",
                        "description": "Include dotfiles (default: false)",
                    },
                },
                "required": [],
            },
        }

    async def execute(
        self,
        args: dict[str, t.Any],
        context: ToolExecutionContext,
    ) -> str:
        show_hidden = bool(args.get("show_hidden", False))
        try:
            if context.tree is not None:
                return await self._ls_tree(args, context, show_hidden)
            return self._ls_host(args, context, show_hidden)
        except ToolError:
            raise
        except Exception as e:
            raise ToolError(f"Failed to list directory: {e}") from e

    async def _ls_tree(
        self,
        args: dict[str, t.Any],
        context: ToolExecutionContext,
        show_hidden: bool,
    ) -> str:
        rel = workspace_relpath(args.get("path", ".") or ".", default=".")
        # Root listing: empty rel means workspace root.
        prefix = "" if rel in ("", ".") else rel.rstrip("/") + "/"
        all_paths = await context.tree.list_paths()
        children: dict[str, str] = {}
        for path in all_paths:
            if prefix and not path.startswith(prefix):
                continue
            rest = path[len(prefix) :] if prefix else path
            if not rest:
                continue
            name, _, remainder = rest.partition("/")
            if not show_hidden and name.startswith("."):
                continue
            kind = "d" if remainder else "f"
            # Prefer directory marker if any nested file exists.
            if name not in children or kind == "d":
                children[name] = kind
        label = rel if rel not in ("", ".") else "."
        lines = [f"Directory listing: {label}", ""]
        if not children:
            lines.append("(empty directory)")
            return "\n".join(lines)
        for name in sorted(children, key=lambda n: (children[n] != "d", n.lower())):
            marker = children[name]
            size_str = "<DIR>" if marker == "d" else "file"
            lines.append(f"[{marker}] {name:40} {size_str:>15}")
        return "\n".join(lines)

    def _ls_host(
        self,
        args: dict[str, t.Any],
        context: ToolExecutionContext,
        show_hidden: bool,
    ) -> str:
        resolved = resolve_in_workspace(
            context.working_directory, args.get("path", ".")
        )
        if not resolved.exists():
            raise ToolError(f"Path not found: {resolved}")
        if not resolved.is_dir():
            raise ToolError(f"Not a directory: {resolved}")

        entries = list(resolved.iterdir())
        if not show_hidden:
            entries = [e for e in entries if not e.name.startswith(".")]
        entries.sort(key=lambda e: (not e.is_dir(), e.name.lower()))

        lines = [f"Directory listing: {resolved}", ""]
        if not entries:
            lines.append("(empty directory)")
            return "\n".join(lines)

        for entry in entries:
            try:
                stat = entry.stat()
                size = stat.st_size
                mtime = datetime.fromtimestamp(stat.st_mtime).strftime(
                    "%Y-%m-%d %H:%M"
                )
                is_dir = entry.is_dir()
                type_marker = "d" if is_dir else "f"
                size_str = "<DIR>" if is_dir else f"{size:,} bytes"
                lines.append(
                    f"[{type_marker}] {entry.name:40} {size_str:>15} {mtime}"
                )
            except OSError:
                lines.append(f"[?] {entry.name:40} {'<ERROR>':>15}")
        return "\n".join(lines)
