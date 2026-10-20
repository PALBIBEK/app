from __future__ import annotations

from typing import Any

from supervisor.tools.args import ToolArgs, path_field
from supervisor.tools.context import ToolContext
from supervisor.tools.errors import InvalidArguments
from supervisor.tools.registry import tool
from supervisor.tools.spec import ToolClass


class EditArgs(ToolArgs):
    file_path: str = path_field("file_path", "path")
    """Path to file (absolute under workspace, or relative)"""

    old_text: str
    """Exact text to replace, including whitespace; must occur in the file"""

    new_text: str
    """Text to put in its place"""

    replace_all: bool = False
    """Replace every occurrence instead of requiring exactly one (default: false)"""


@tool(tool_class=ToolClass.IDEMPOTENT)
async def edit(args: EditArgs, ctx: ToolContext) -> dict[str, Any]:
    """Replace exact text in a workspace file. Fails unless old_text occurs exactly once, or replace_all is set.

    Requiring a unique match is what makes this safe to retry and safe to
    trust: an ambiguous edit is refused with the occurrence count, so the
    model widens ``old_text`` instead of silently changing the wrong spot.
    """
    if not args.old_text:
        raise InvalidArguments("old_text must not be empty")
    workspace = ctx.workspace
    rel = workspace.relpath(args.file_path)
    try:
        text = (await workspace.read_bytes(rel)).decode("utf-8")
    except UnicodeDecodeError:
        raise InvalidArguments(f"{rel} is not UTF-8 text; edit only changes text files") from None
    count = text.count(args.old_text)
    if count == 0:
        raise InvalidArguments(f"old_text does not occur in {rel}")
    if count > 1 and not args.replace_all:
        raise InvalidArguments(
            f"old_text occurs {count} times in {rel}; include more context or set replace_all"
        )
    updated = text.replace(args.old_text, args.new_text)
    await workspace.write_bytes(rel, updated.encode("utf-8"))
    ctx.mutated()
    return {"path": rel, "replacements": count}
