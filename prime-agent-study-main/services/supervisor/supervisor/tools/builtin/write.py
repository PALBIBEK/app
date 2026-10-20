from __future__ import annotations

from typing import Any

from supervisor.tools.args import ToolArgs, path_field
from supervisor.tools.context import ToolContext
from supervisor.tools.registry import tool
from supervisor.tools.spec import ToolClass


class WriteArgs(ToolArgs):
    file_path: str = path_field("file_path", "path")
    """Path to file (absolute under workspace, or relative)"""

    content: str
    """Content to write"""


@tool(tool_class=ToolClass.IDEMPOTENT)
async def write(args: WriteArgs, ctx: ToolContext) -> dict[str, Any]:
    """Write or overwrite a file in the workspace. Creates parent directories if needed."""
    workspace = ctx.workspace
    rel = workspace.relpath(args.file_path)
    data = args.content.encode("utf-8")
    await workspace.write_bytes(rel, data)
    ctx.mutated()
    return {"path": rel, "bytes": len(data)}
