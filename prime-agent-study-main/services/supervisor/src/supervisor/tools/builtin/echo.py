from __future__ import annotations

from supervisor.tools.args import ToolArgs
from supervisor.tools.context import ToolContext
from supervisor.tools.registry import tool
from supervisor.tools.spec import ToolClass


class EchoArgs(ToolArgs):
    text: str = ""


@tool(tool_class=ToolClass.PURE)
async def echo(args: EchoArgs, ctx: ToolContext) -> dict[str, str]:
    """Echo text (dev / smoke tool)"""
    return {"text": args.text}
