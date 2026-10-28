from __future__ import annotations

import hashlib
from typing import Any, Final

from pydantic import Field

from supervisor.tools.args import ToolArgs
from supervisor.tools.context import ToolContext
from supervisor.tools.errors import InvalidArguments
from supervisor.tools.registry import tool
from supervisor.tools.spec import ToolClass
from supervisor.tools.truncate import truncate_tail

_GRACE_S: Final = 2.0
"""Kept back from the call's budget so the command is killed (and its partial
output returned) before the tool call itself times out."""
OUTPUT_DIR: Final = ".githi/output"
"""Where a command's full output is kept when the result shows only part of it (pi
saves it to a temp file; here it is the workspace, so ``read`` and ``grep`` reach it)."""
MAX_SPILL_BYTES: Final = 8 * 1024 * 1024


class BashArgs(ToolArgs):
    command: str
    """Shell command to execute"""

    timeout: int | None = Field(default=None, ge=1)
    """Timeout in seconds (optional, no default timeout)"""


@tool(tool_class=ToolClass.NON_ATOMIC)
async def bash(args: BashArgs, ctx: ToolContext) -> dict[str, Any]:
    """Execute a bash command in the current working directory. Returns stdout and stderr. Output is truncated to last 2000 lines or 50KB (whichever is hit first). If truncated, full output is saved to a temp file. Optionally provide a timeout in seconds.

    As pi's bash: stdout and stderr as one stream in the order written (``2>&1``),
    the last 2000 lines or 50 KB kept, the whole output saved to a file the result
    names; a non-zero exit or a timeout is a failed result that still carries the
    output. The command runs in the workspace root with a scrubbed environment (no
    supervisor credentials); a requested timeout is capped by the call's budget, and
    the cap is reported, so a killed command is not mistaken for a slow one.
    """
    if not args.command.strip():
        raise InvalidArguments("command must not be empty")
    ceiling = max(ctx.timeout_s - _GRACE_S, 1.0)
    timeout = min(float(args.timeout), ceiling) if args.timeout else ceiling

    result = await ctx.workspace.run(f"exec 2>&1\n{args.command}", timeout_s=timeout, env=ctx.env)
    ctx.mutated()  # any command may have changed files

    raw = (
        result.full.decode("utf-8", errors="replace")
        if result.truncated and result.full
        else result.stdout + (f"\n{result.stderr}" if result.stderr else "")
    )
    shown = truncate_tail(raw)
    payload: dict[str, Any] = {
        "exit_code": result.exit_code,
        "output": shown.content,
        "stdout": shown.content,  # what clients of the older shape read
        "stderr": "",
    }
    if shown.truncated:
        payload["truncation"] = {
            "by": shown.truncated_by,
            "start": shown.total_lines - shown.output_lines + 1,
            "end": shown.total_lines,
            "total": shown.total_lines,
            "shown_bytes": shown.output_bytes,
            "partial": shown.last_line_partial,
        }
        if shown.last_line_partial:
            payload["truncation"]["line_bytes"] = len(raw.rstrip("\n").rsplit("\n", 1)[-1].encode("utf-8"))
        if spill := await _spill(ctx, raw.encode("utf-8")):
            payload["full_output"] = spill
    if result.timed_out:
        payload["timed_out"] = True
        payload["timeout_s"] = timeout
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
