from __future__ import annotations

from supervisor.tools.args import ToolArgs, path_field
from supervisor.tools.context import ToolContext
from supervisor.tools.registry import tool
from supervisor.tools.spec import ToolClass


class LsArgs(ToolArgs):
    path: str = path_field("path", "file_path", default="")
    """Directory path (default: workspace root)"""

    show_hidden: bool = False
    """Include dotfiles (default: false)"""


@tool(tool_class=ToolClass.PURE)
async def ls(args: LsArgs, ctx: ToolContext) -> list[str]:
    """List a directory in the workspace. Directory names end with '/'."""
    entries = await ctx.workspace.list_dir(args.path)
    return [str(entry) for entry in entries if args.show_hidden or not entry.name.startswith(".")]
