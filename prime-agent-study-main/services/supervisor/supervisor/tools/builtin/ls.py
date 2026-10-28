"""ls: a directory's entries, as pi's ls returns them.

Sorted alphabetically (ignoring case), ``/`` after a directory's name, dotfiles
included, at most ``limit`` entries and 50 KB, with a closing note naming each limit hit.
"""

from __future__ import annotations

import sys
from typing import Final

from pydantic.json_schema import SkipJsonSchema

from supervisor.tools.args import ToolArgs, path_field
from supervisor.tools.context import ToolContext
from supervisor.tools.errors import FileMissing, InvalidArguments
from supervisor.tools.registry import tool
from supervisor.tools.spec import ToolClass
from supervisor.tools.truncate import DEFAULT_MAX_BYTES, format_size, truncate_head

DEFAULT_LIMIT: Final = 500


class LsArgs(ToolArgs):
    path: str = path_field("path", "file_path", default="")
    """Directory to list (default: current directory)"""

    limit: int | None = None
    """Maximum number of entries to return (default: 500)"""

    # Older pinned tool sets offered this; dotfiles are always listed now (as pi lists them).
    show_hidden: SkipJsonSchema[bool | None] = None


@tool(tool_class=ToolClass.PURE)
async def ls(args: LsArgs, ctx: ToolContext) -> str:
    """List directory contents. Returns entries sorted alphabetically, with '/' suffix for directories. Includes dotfiles. Output is truncated to 500 entries or 50KB (whichever is hit first)."""
    workspace = ctx.workspace
    rel = workspace.relpath(args.path)
    try:
        entries = await workspace.list_dir(args.path)
    except FileMissing:
        if await _is_file(ctx, args.path):
            raise InvalidArguments(f"Not a directory: {rel}") from None
        raise FileMissing(rel or ".", kind="path") from None
    if not entries:
        return "(empty directory)"
    limit = max(1, args.limit or DEFAULT_LIMIT)
    names = [str(entry) for entry in sorted(entries, key=lambda entry: entry.name.lower())]
    reached = len(names) > limit
    page = truncate_head("\n".join(names[:limit]), max_lines=sys.maxsize)
    notes: list[str] = []
    if reached:
        notes.append(f"{limit} entries limit reached. Use limit={limit * 2} for more")
    if page.truncated:
        notes.append(f"{format_size(DEFAULT_MAX_BYTES)} limit reached")
    return page.content + (f"\n\n[{'. '.join(notes)}]" if notes else "")


async def _is_file(ctx: ToolContext, path: str) -> bool:
    try:
        await ctx.workspace.read_bytes(path)
    except Exception:  # noqa: BLE001 — missing, or not a file: either way not a file
        return False
    return True
