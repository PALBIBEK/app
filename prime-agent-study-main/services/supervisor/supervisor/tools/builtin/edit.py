from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from supervisor.tools.args import ToolArgs, path_field
from supervisor.tools.context import ToolContext
from supervisor.tools.edit_match import Edit, EditError, apply_edits, unified_diff
from supervisor.tools.errors import InvalidArguments
from supervisor.tools.registry import tool
from supervisor.tools.spec import ToolClass


class EditPair(BaseModel):
    old_text: str
    """Exact text to replace"""

    new_text: str
    """Text to put in its place"""


class EditArgs(ToolArgs):
    file_path: str = path_field("file_path", "path")
    """Path to file (absolute under workspace, or relative)"""

    old_text: str | None = None
    """Exact text to replace, including whitespace; must occur in the file"""

    new_text: str | None = None
    """Text to put in its place"""

    replace_all: bool = False
    """Replace every occurrence instead of requiring exactly one (default: false)"""

    edits: list[EditPair] | None = Field(default=None, max_length=50)
    """Several replacements applied together, all or none; use instead of old_text/new_text"""


@tool(tool_class=ToolClass.IDEMPOTENT)
async def edit(args: EditArgs, ctx: ToolContext) -> dict[str, Any]:
    """Replace exact text in a workspace file. Each old_text must occur exactly once unless replace_all is set; pass edits to make several replacements at once. Read the file first.

    Requiring a unique match is what makes this safe to retry and safe to trust: an
    ambiguous edit is refused with the occurrence count, so the model widens
    ``old_text`` instead of silently changing the wrong spot. Line endings, a BOM and
    curly quotes in the file are matched and kept (``supervisor.tools.edit_match``).
    Returns the change as a diff.
    """
    planned = [Edit(p.old_text, p.new_text, args.replace_all) for p in args.edits or []]
    if args.old_text is not None or args.new_text is not None:
        if args.old_text is None or args.new_text is None:
            raise InvalidArguments("old_text and new_text go together")
        planned.insert(0, Edit(args.old_text, args.new_text, args.replace_all))
    workspace = ctx.workspace
    rel = workspace.relpath(args.file_path)
    try:
        text = (await workspace.read_bytes(rel)).decode("utf-8")
    except UnicodeDecodeError:
        raise InvalidArguments(f"{rel} is not UTF-8 text; edit only changes text files") from None
    try:
        updated, count = apply_edits(text, planned, rel)
    except EditError as exc:
        raise InvalidArguments(str(exc)) from None
    await workspace.write_bytes(rel, updated.encode("utf-8"))
    ctx.mutated()
    return {"path": rel, "replacements": count, "diff": unified_diff(text, updated, rel)}
