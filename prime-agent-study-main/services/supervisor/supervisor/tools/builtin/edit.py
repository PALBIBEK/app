from __future__ import annotations

import json
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator
from pydantic.json_schema import SkipJsonSchema

from supervisor.tools.args import ToolArgs, path_field
from supervisor.tools.context import ToolContext
from supervisor.tools.edit_match import Edit, EditError, apply_edits, unified_diff
from supervisor.tools.errors import InvalidArguments
from supervisor.tools.registry import tool
from supervisor.tools.spec import ToolClass


class ReplaceEdit(BaseModel):
    """One targeted replacement (pi's ``edits[]`` item; ``old_text``/``new_text`` too).

    The fields carry pi's names, so a journalled call's arguments read back as sent.
    """

    model_config = ConfigDict(use_attribute_docstrings=True, extra="ignore", frozen=True)

    oldText: str = Field(validation_alias=AliasChoices("oldText", "old_text"))
    """Exact text for one targeted replacement. It must be unique in the original file and must not overlap with any other edits[].oldText in the same call."""

    newText: str = Field(validation_alias=AliasChoices("newText", "new_text"))
    """Replacement text for this targeted edit."""


def _require_edits(schema: dict[str, Any]) -> None:
    required = schema.setdefault("required", [])
    if "edits" not in required:
        required.append("edits")


class EditArgs(ToolArgs):
    """pi's edit arguments: ``path`` and ``edits[{oldText, newText}]``.

    Also accepted, unpublished: what tool sets pinned before pi's schema send
    (``file_path``, a top-level ``old_text``/``new_text``, ``replace_all``) and what some
    models send instead of an array (``edits`` as a JSON string, or one edit object),
    as pi's ``prepareArguments`` accepts it.
    """

    model_config = ConfigDict(json_schema_extra=_require_edits)

    file_path: str = path_field("path", "file_path")
    """Path to the file to edit (relative or absolute)"""

    edits: list[ReplaceEdit] = Field(default_factory=list)
    """One or more targeted replacements. Each edit is matched against the original file, not incrementally. Do not include overlapping or nested edits. If two changes touch the same block or nearby lines, merge them into one edit instead."""

    old_text: SkipJsonSchema[str | None] = Field(default=None, validation_alias=AliasChoices("old_text", "oldText"))
    new_text: SkipJsonSchema[str | None] = Field(default=None, validation_alias=AliasChoices("new_text", "newText"))
    replace_all: SkipJsonSchema[bool] = False

    @model_validator(mode="before")
    @classmethod
    def _edits_as_pi_accepts_them(cls, data: Any) -> Any:
        if not isinstance(data, dict) or "edits" not in data:
            return data
        edits = data["edits"]
        if isinstance(edits, str):
            try:
                edits = json.loads(edits)
            except ValueError:
                return data
        if isinstance(edits, dict):
            edits = [edits]
        return {**data, "edits": edits}


@tool(tool_class=ToolClass.IDEMPOTENT)
async def edit(args: EditArgs, ctx: ToolContext) -> dict[str, Any]:
    """Edit a single file using exact text replacement. Every edits[].oldText must match a unique, non-overlapping region of the original file. If two changes affect the same block or nearby lines, merge them into one edit instead of emitting overlapping edits. Do not include large unchanged regions just to connect distant changes.

    Requiring a unique match is what makes this safe to retry and safe to trust: an
    ambiguous edit is refused with the occurrence count, so the model widens its
    text instead of silently changing the wrong spot. Matching is pi's: exact first,
    then tolerant of trailing whitespace and typographic look-alikes, keeping every
    untouched line's bytes (``supervisor.tools.edit_match``). The model is told how
    many blocks changed; the diff is kept for people.
    """
    planned = [Edit(e.oldText, e.newText, args.replace_all) for e in args.edits]
    if args.old_text is not None or args.new_text is not None:
        if args.old_text is None or args.new_text is None:
            raise InvalidArguments("oldText and newText go together")
        planned.append(Edit(args.old_text, args.new_text, args.replace_all))
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
