"""Where a command's whole output is kept when its result shows only part of it.

pi saves it to a temp file; here it is the workspace, so ``read`` and ``grep`` reach it.
"""

from __future__ import annotations

import hashlib
from typing import Final

from supervisor.tools.context import ToolContext

__all__ = ["MAX_SPILL_BYTES", "OUTPUT_DIR", "spill"]

OUTPUT_DIR: Final = ".githi/output"
MAX_SPILL_BYTES: Final = 8 * 1024 * 1024


async def spill(ctx: ToolContext, full: bytes | None, *, kind: str) -> str | None:
    """Keep a cut output whole in the workspace, named by ``kind`` and its content; its
    path, or None (nothing to keep, or the write failed: the result stands without it)."""
    if not full:
        return None
    name = f"{OUTPUT_DIR}/{kind}-{hashlib.sha256(full).hexdigest()[:12]}.log"
    data = full if len(full) <= MAX_SPILL_BYTES else full[:MAX_SPILL_BYTES] + b"\n[... output past 8 MiB not kept]\n"
    try:
        await ctx.workspace.write_bytes(name, data)
    except Exception:  # noqa: BLE001 — the command's result stands without its copy
        return None
    return name
