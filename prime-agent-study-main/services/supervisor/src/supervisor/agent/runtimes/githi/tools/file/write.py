"""Write file tool — TreeIO / path-confined (WP-M5)."""

from __future__ import annotations

import typing as t

from supervisor.agent.runtimes.githi.tools.base import (
    Tool,
    ToolError,
    ToolExecutionContext,
)
from supervisor.agent.runtimes.githi.tools.paths import resolve_in_workspace
from supervisor.agent.runtimes.githi.tools.workspace_paths import workspace_relpath


class WriteTool(Tool):
    """Write/overwrite a file under the session workspace."""

    name = "write"

    def get_schema(self) -> dict[str, t.Any]:
        return {
            "name": "write",
            "description": (
                "Write or overwrite a file in the workspace. "
                "Creates parent directories if needed."
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
                    "content": {
                        "type": "string",
                        "description": "Content to write",
                    },
                },
                "required": ["file_path", "content"],
            },
        }

    async def execute(
        self,
        args: dict[str, t.Any],
        context: ToolExecutionContext,
    ) -> str:
        content = args["content"]
        try:
            if context.tree is not None:
                rel = workspace_relpath(args["file_path"])
                data = content.encode("utf-8")
                await context.tree.write_bytes(rel, data)
                if context.on_mutate is not None:
                    context.on_mutate()
                return f"File written successfully: {rel} ({len(data)} bytes)"

            resolved = resolve_in_workspace(
                context.working_directory, args["file_path"]
            )
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_text(content, encoding="utf-8")
            file_size = resolved.stat().st_size
            if context.on_mutate is not None:
                context.on_mutate()
            return f"File written successfully: {resolved} ({file_size} bytes)"
        except ToolError:
            raise
        except Exception as e:
            raise ToolError(f"Failed to write file {args['file_path']}: {e}") from e
