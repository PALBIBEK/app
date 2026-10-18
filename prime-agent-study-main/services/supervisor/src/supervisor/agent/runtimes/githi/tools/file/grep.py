"""Grep / search files tool — TreeIO / path-confined (D6)."""

from __future__ import annotations

import fnmatch
import re
import typing as t
from pathlib import Path

from supervisor.agent.runtimes.githi.tools.base import (
    Tool,
    ToolError,
    ToolExecutionContext,
)
from supervisor.agent.runtimes.githi.tools.paths import resolve_in_workspace
from supervisor.agent.runtimes.githi.tools.workspace_paths import workspace_relpath

# Cap matches so a broad pattern cannot blow the agent context window.
_DEFAULT_MAX_MATCHES = 100
_MAX_FILE_BYTES = 1_000_000


class GrepTool(Tool):
    """Search file contents under the session workspace (pure / read-only)."""

    name = "grep"

    def get_schema(self) -> dict[str, t.Any]:
        return {
            "name": "grep",
            "description": (
                "Search files in the workspace for a regex/string pattern. "
                "Returns matching lines with path and line number."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Regex or literal pattern to search for",
                    },
                    "path": {
                        "type": "string",
                        "description": "Directory or file to search (default: workspace root)",
                    },
                    "glob": {
                        "type": "string",
                        "description": "Optional fnmatch filter (e.g. '*.py')",
                    },
                    "case_insensitive": {
                        "type": "boolean",
                        "description": "Case-insensitive match (default: false)",
                    },
                    "max_matches": {
                        "type": "integer",
                        "description": f"Max matches to return (default: {_DEFAULT_MAX_MATCHES})",
                        "minimum": 1,
                    },
                },
                "required": ["pattern"],
            },
        }

    async def execute(
        self,
        args: dict[str, t.Any],
        context: ToolExecutionContext,
    ) -> dict[str, t.Any]:
        pattern = str(args.get("pattern") or "")
        if not pattern:
            raise ToolError("pattern is required")
        flags = re.IGNORECASE if args.get("case_insensitive") else 0
        try:
            regex = re.compile(pattern, flags)
        except re.error as exc:
            raise ToolError(f"Invalid regex pattern: {exc}") from exc

        path_arg = str(args.get("path") or args.get("file_path") or ".")
        glob_pat = args.get("glob")
        glob_filter = str(glob_pat) if glob_pat else None
        try:
            max_matches = int(args.get("max_matches") or _DEFAULT_MAX_MATCHES)
        except (TypeError, ValueError):
            max_matches = _DEFAULT_MAX_MATCHES
        max_matches = max(1, min(max_matches, 1000))

        try:
            if context.tree is not None:
                matches = await self._grep_tree(
                    context, regex, path_arg, glob_filter, max_matches
                )
            else:
                matches = self._grep_host(
                    context, regex, path_arg, glob_filter, max_matches
                )
        except ToolError:
            raise
        except Exception as e:
            raise ToolError(f"grep failed: {e}") from e

        return {
            "pattern": pattern,
            "matches": matches,
            "count": len(matches),
            "truncated": len(matches) >= max_matches,
        }

    async def _grep_tree(
        self,
        context: ToolExecutionContext,
        regex: re.Pattern[str],
        path_arg: str,
        glob_filter: str | None,
        max_matches: int,
    ) -> list[dict[str, t.Any]]:
        rel = workspace_relpath(path_arg, default=".")
        prefix = "" if rel in ("", ".") else rel.rstrip("/")
        all_paths = await context.tree.list_paths()
        matches: list[dict[str, t.Any]] = []
        for path in sorted(all_paths):
            if prefix and path != prefix and not path.startswith(prefix + "/"):
                continue
            if glob_filter and not fnmatch.fnmatch(Path(path).name, glob_filter):
                continue
            try:
                data = await context.tree.read_bytes(path)
            except Exception:
                continue
            if len(data) > _MAX_FILE_BYTES:
                continue
            text = data.decode("utf-8", errors="replace")
            self._collect_matches(path, text, regex, matches, max_matches)
            if len(matches) >= max_matches:
                break
        return matches

    def _grep_host(
        self,
        context: ToolExecutionContext,
        regex: re.Pattern[str],
        path_arg: str,
        glob_filter: str | None,
        max_matches: int,
    ) -> list[dict[str, t.Any]]:
        root = resolve_in_workspace(context.working_directory, path_arg)
        if not root.exists():
            raise ToolError(f"Path not found: {root}")
        files: list[Path]
        if root.is_file():
            files = [root]
        elif root.is_dir():
            files = [p for p in root.rglob("*") if p.is_file()]
        else:
            raise ToolError(f"Not a file or directory: {root}")

        matches: list[dict[str, t.Any]] = []
        workspace = Path(context.working_directory).resolve()
        for file_path in sorted(files):
            if glob_filter and not fnmatch.fnmatch(file_path.name, glob_filter):
                continue
            try:
                if file_path.stat().st_size > _MAX_FILE_BYTES:
                    continue
                text = file_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            try:
                display = str(file_path.resolve().relative_to(workspace))
            except ValueError:
                display = str(file_path)
            self._collect_matches(display, text, regex, matches, max_matches)
            if len(matches) >= max_matches:
                break
        return matches

    @staticmethod
    def _collect_matches(
        path: str,
        text: str,
        regex: re.Pattern[str],
        matches: list[dict[str, t.Any]],
        max_matches: int,
    ) -> None:
        for i, line in enumerate(text.splitlines(), start=1):
            if regex.search(line):
                matches.append({"path": path, "line": i, "text": line[:500]})
                if len(matches) >= max_matches:
                    return
