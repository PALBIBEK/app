"""edit -- exact-string replacement in a file.

Exact match rather than line numbers, matching pi's own edit tool. Line
numbers drift the moment anything above them changes; an exact string either
matches what the model read or it does not, and the failure is loud.

The matching, line-ending and BOM handling live in `edit_diff`. What this
module owns is the tool contract: sandbox confinement, reading and writing
bytes, and returning a diff the model can check its own work against.

Reading and writing **bytes** is load-bearing. `Path.read_text` translates
`\\r\\n` to `\\n` and `write_text` translates back to `os.linesep`, so a
one-word edit to a CRLF file on a Linux host silently rewrites every line
ending in it. The tool decodes explicitly, normalizes for matching, and
restores the original ending and BOM before writing.
"""

from __future__ import annotations

from typing import Any

from ..errors import SandboxViolation
from ..harness.env import FileErrorCode
from ..sandbox.paths import ensure_writable, resolve_in_root
from .base import Tool, ToolContext, ToolResult, fail, ok
from .edit_diff import (
    Edit,
    apply_edits,
    detect_line_ending,
    generate_diff_string,
    normalize_to_lf,
    restore_line_endings,
    strip_bom,
)


def _parse_edits(args: dict[str, Any]) -> list[Edit] | str:
    """Accept one edit or a batch. Returns the edits, or an error message.

    A batch exists because the model routinely knows several changes to one
    file at once, and one call that either fully applies or fully fails beats
    N calls that can leave the file half-edited.
    """
    if "edits" in args:
        raw = args["edits"]
        if not isinstance(raw, list) or not raw:
            return "`edits` must be a non-empty list of {old, new} objects."
        edits = []
        for index, item in enumerate(raw):
            if not isinstance(item, dict):
                return f"edits[{index}] must be an object with replacement fields."
            old = item.get("old", item.get("oldText"))
            new = item.get("new", item.get("newText"))
            if old is None or new is None:
                return f"edits[{index}] must have `old`/`new` or `oldText`/`newText`."
            edits.append(Edit(str(old), str(new)))
        return edits

    old = args.get("old", args.get("oldText"))
    new = args.get("new", args.get("newText"))
    if old is None or new is None:
        return "edit requires `old`/`new` or `oldText`/`newText`, or an `edits` list."
    return [Edit(str(old), str(new))]


def edit_tool() -> Tool:
    def run(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        raw = args.get("path")
        if not raw:
            return fail("edit requires a `path`.")

        edits = _parse_edits(args)
        if isinstance(edits, str):
            return fail(edits)

        try:
            path = resolve_in_root(str(raw), ctx.policy)
            ensure_writable(path, ctx.policy)
        except SandboxViolation as exc:
            return fail(str(exc))

        read = ctx.env.read_binary_file(str(path))
        if not read.is_ok:
            if read.error.code is FileErrorCode.NOT_FOUND:
                return fail(f"no such file: {raw}")
            return fail(f"could not read {raw}: {read.error}")
        original = read.value.decode("utf-8", errors="replace")

        bom, text = strip_bom(original)
        ending = detect_line_ending(text)
        normalized = normalize_to_lf(text)

        if args.get("replace_all"):
            # An explicit opt-out of the uniqueness check. Kept separate from
            # `apply_edits`, which refuses ambiguity by design -- the caller
            # asking for every occurrence has already answered that question.
            new_normalized, count = _replace_all(normalized, edits)
            if count == 0:
                return fail(f"`old` string not found in {raw}.")
        else:
            try:
                new_normalized = apply_edits(normalized, edits, str(raw)).new_content
            except ValueError as exc:
                # Nothing is written on failure: a half-applied batch leaves
                # the file in a state neither the model nor the user asked for.
                return fail(str(exc))
            count = len(edits)

        diff, first_line = generate_diff_string(normalized, new_normalized)
        payload = bom + restore_line_endings(new_normalized, ending)

        # Bytes, not text: the whole point of restoring `ending` and `bom`
        # above is defeated if the backend re-translates newlines on write.
        written = ctx.env.write_file(str(path), payload.encode("utf-8"))
        if not written.is_ok:
            return fail(f"could not write {raw}: {written.error}")

        header = f"applied {count} edit(s) to {raw}"
        if first_line is not None:
            header += f" (first change at line {first_line})"
        return ok(f"{header}\n\n{diff}", replacements=count, first_changed_line=first_line)

    return Tool(
        name="edit",
        description="Replace exact text in a file. Each `old` must match the file "
                    "and be unique unless replace_all is set. Pass `edits` to apply "
                    "several replacements atomically. Read the file first.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old": {"type": "string", "description": "exact text to replace"},
                "new": {"type": "string", "description": "replacement text"},
                "oldText": {"type": "string", "description": "Pi-compatible alias for old"},
                "newText": {"type": "string", "description": "Pi-compatible alias for new"},
                "edits": {
                    "type": "array",
                    "description": "several replacements applied atomically; use "
                                   "instead of old/new",
                    "items": {
                        "type": "object",
                        "properties": {"old": {"type": "string"},
                                       "new": {"type": "string"}},
                        "required": ["old", "new"],
                    },
                },
                "replace_all": {"type": "boolean"},
            },
            "required": ["path"],
        },
        run=run,
    )


def _replace_all(content: str, edits: list[Edit]) -> tuple[str, int]:
    count = 0
    for edit in edits:
        count += content.count(edit.old_text)
        content = content.replace(edit.old_text, edit.new_text)
    return content, count
