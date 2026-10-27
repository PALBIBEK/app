from __future__ import annotations

import hashlib
from typing import Any, Final

from pydantic import Field

from supervisor.tools.args import ToolArgs
from supervisor.tools.context import ToolContext
from supervisor.tools.errors import InvalidArguments
from supervisor.tools.registry import tool
from supervisor.tools.spec import ToolClass

_GRACE_S: Final = 2.0
OUTPUT_DIR: Final = ".githi/output"
"""Where a command's full output is kept when the result shows only part of it (pi
saves it to a temp file; here it is the workspace, so ``read`` and ``grep`` reach it)."""
MAX_SPILL_BYTES: Final = 8 * 1024 * 1024
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
        spill = await _spill(ctx, result.full)
        payload["notice"] = (
            f"output was long, so its middle was cut; the full output is in {spill}: "
            "search it with grep, or read it with offset and limit"
            if spill
            else "output was long, so its middle was cut; to see all of it, redirect it to a "
            "file (cmd > out.txt 2>&1) and read it with grep, head, tail or sed"
        )
        if spill:
            payload["full_output"] = spill
    if result.timed_out:
        payload["timed_out"] = True
        payload["notice"] = f"killed after {timeout:g}s"
    if args.timeout and args.timeout > ceiling:
        payload["timeout_capped_to"] = ceiling
    return payload


async def _spill(ctx: ToolContext, full: bytes | None) -> str | None:
    """Keep a cut command's whole output in the workspace; its path, or None."""
    if not full:
        return None
    name = f"{OUTPUT_DIR}/bash-{hashlib.sha256(full).hexdigest()[:12]}.log"
    data = full if len(full) <= MAX_SPILL_BYTES else full[:MAX_SPILL_BYTES] + b"\n[... output past 8 MiB not kept]\n"
    try:
        await ctx.workspace.write_bytes(name, data)
    except Exception:  # noqa: BLE001 — the command's result stands without its copy
        return None
    return name
