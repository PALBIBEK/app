from __future__ import annotations

from pydantic import Field

from supervisor.tools.args import ToolArgs, path_field
from supervisor.tools.context import ToolContext
from supervisor.tools.registry import tool
from supervisor.tools.spec import ToolClass


class ReadArgs(ToolArgs):
    file_path: str = path_field("file_path", "path")
    """Path to file (absolute under workspace, or relative)"""

    offset: int | None = Field(default=None, ge=1)
    """1-indexed start line (optional)"""

    limit: int | None = Field(default=None, ge=1)
    """Max lines to read (optional)"""


@tool(tool_class=ToolClass.PURE)
async def read(args: ReadArgs, ctx: ToolContext) -> str:
    """Read file contents from the workspace. Returns the file contents as a string.

    Undecodable bytes are replaced rather than failing the call, so a model
    that opens a binary file learns that from the output.
    """
    data = await ctx.workspace.read_bytes(args.file_path)
    text = data.decode("utf-8", errors="replace")
    if args.offset is None and args.limit is None:
        return text
    lines = text.splitlines(keepends=True)
    start = (args.offset or 1) - 1
    stop = None if args.limit is None else start + args.limit
    return "".join(lines[start:stop])
