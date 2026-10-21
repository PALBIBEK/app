from __future__ import annotations

from typing import Any, Final

from pydantic import Field

from supervisor.tools.args import ToolArgs
from supervisor.tools.context import ToolContext
from supervisor.tools.errors import InvalidArguments
from supervisor.tools.registry import tool
from supervisor.tools.spec import ToolClass

_GRACE_S: Final = 2.0
"""Kept back from the call's budget so the command is killed (and its partial
output returned) before the tool call itself times out."""


class BashArgs(ToolArgs):
    command: str
    """The bash command to run"""

    timeout: int | None = Field(default=None, ge=1)
    """Seconds before the command is killed (capped by the session's limit)"""


@tool(tool_class=ToolClass.NON_ATOMIC)
async def bash(args: BashArgs, ctx: ToolContext) -> dict[str, Any]:
    """Run a shell command in the workspace. Returns exit code, stdout and stderr.

    The command runs in the workspace root with a scrubbed environment (no
    supervisor credentials). A requested timeout is a request, not an
    override: it is capped by the call's budget and the cap is reported, so a
    killed command is not mistaken for a slow one.
    """
    if not args.command.strip():
        raise InvalidArguments("command must not be empty")
    ceiling = max(ctx.timeout_s - _GRACE_S, 1.0)
    timeout = min(float(args.timeout), ceiling) if args.timeout else ceiling

    result = await ctx.workspace.run(args.command, timeout_s=timeout, env=ctx.env)
    ctx.mutated()  # any command may have changed files

    payload: dict[str, Any] = {
        "exit_code": result.exit_code,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }
    if result.truncated:
        payload["notice"] = (
            "output was long, so its middle was cut; to see all of it, redirect it to a file "
            "(cmd > out.txt 2>&1) and read it with grep, head, tail or sed"
        )
    if result.timed_out:
        payload["timed_out"] = True
        payload["notice"] = f"killed after {timeout:g}s"
    if args.timeout and args.timeout > ceiling:
        payload["timeout_capped_to"] = ceiling
    return payload
