"""Read file tool — TreeIO / path-confined (WP-M5)."""

from __future__ import annotations

import typing as t

from supervisor.agent.runtimes.githi.tools.base import (
    Tool,
    ToolError,
    ToolExecutionContext,
)
from supervisor.agent.runtimes.githi.tools.paths import resolve_in_workspace
from supervisor.agent.runtimes.githi.tools.workspace_paths import workspace_relpath


class ReadTool(Tool):
    """Read file contents under the session workspace."""

    name = "read"

    def get_schema(self) -> dict[str, t.Any]:
        return {
            "name": "read",
            "description": (
                "Read file contents from the workspace. "
                "Returns the file contents as a string."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": (
                            "Path to file (absolute under workspace, or relative)"
                        ),
                    },
                    "offset": {
                        "type": "integer",
                        "description": "1-indexed start line (optional)",
                        "minimum": 1,
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max lines to read (optional)",
                        "minimum": 1,
                    },
                },
                "required": ["file_path"],
            },
        }

    async def execute(
        self,
        args: dict[str, t.Any],
        context: ToolExecutionContext,
    ) -> str:
        offset = args.get("offset")
        limit = args.get("limit")
        try:
            if context.tree is not None:
                rel = workspace_relpath(args["file_path"])
                data = await context.tree.read_bytes(rel)
                text = data.decode("utf-8", errors="replace")
            else:
                resolved = resolve_in_workspace(
                    context.working_directory, args["file_path"]
                )
                if not resolved.exists():
                    raise ToolError(f"File not found: {resolved}")
                if not resolved.is_file():
                    raise ToolError(f"Not a file: {resolved}")
                text = resolved.read_text(encoding="utf-8", errors="replace")
        except ToolError:
            raise
        except FileNotFoundError as e:
            raise ToolError(f"File not found: {args['file_path']}") from e
        except Exception as e:
            raise ToolError(f"Failed to read file {args['file_path']}: {e}") from e

        if offset is None and limit is None:
            return text
        lines = text.splitlines(keepends=True)
        start_idx = (offset - 1) if offset else 0
        end_idx = (start_idx + limit) if limit else len(lines)
        return "".join(lines[start_idx:end_idx])
