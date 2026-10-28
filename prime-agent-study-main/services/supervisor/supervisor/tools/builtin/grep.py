"""grep: matching lines, as pi's grep (ripgrep) returns them.

``path:line: text`` per matching line (paths relative to the directory searched),
``path-line- text`` for context lines, at most ``limit`` matches and 50 KB, lines cut at
500 characters, each limit hit named in a closing note with how to get more.
``.gitignore`` is respected and dotfiles are searched (rg ``--hidden``); binary files are
skipped. The search runs through the workspace's file API, so it works in a sandbox
without ``rg`` and over a saved file tree alike.
"""

from __future__ import annotations

import re
import sys
from typing import Final

from pydantic import AliasChoices, Field

from supervisor.tools.args import ToolArgs, path_field
from supervisor.tools.context import ToolContext
from supervisor.tools.errors import FileMissing, InvalidArguments
from supervisor.tools.ignore import Ignore, glob_regex
from supervisor.tools.registry import tool
from supervisor.tools.spec import ToolClass
from supervisor.tools.truncate import (
    DEFAULT_MAX_BYTES,
    GREP_MAX_LINE_LENGTH,
    format_size,
    truncate_head,
    truncate_line,
)

DEFAULT_LIMIT: Final = 100
_BINARY_PROBE: Final = 8192


class GrepArgs(ToolArgs):
    pattern: str
    """Search pattern (regex or literal string)"""

    path: str = path_field("path", "file_path", default="")
    """Directory or file to search (default: current directory)"""

    glob: str | None = None
    """Filter files by glob pattern, e.g. '*.ts' or '**/*.spec.ts'"""

    ignore_case: bool = Field(
        default=False,
        validation_alias=AliasChoices("ignoreCase", "ignore_case", "case_insensitive"),
    )
    """Case-insensitive search (default: false)"""

    literal: bool = False
    """Treat pattern as literal string instead of regex (default: false)"""

    context: int | None = None
    """Number of lines to show before and after each match (default: 0)"""

    limit: int | None = Field(default=None, validation_alias=AliasChoices("limit", "max_matches"))
    """Maximum number of matches to return (default: 100)"""


def _globber(glob: str) -> re.Pattern[str]:
    # rg's --glob: a pattern without '/' matches a file's name at any depth.
    return glob_regex(glob if "/" in glob else f"**/{glob}")


@tool(tool_class=ToolClass.PURE)
async def grep(args: GrepArgs, ctx: ToolContext) -> str:
    """Search file contents for a pattern. Returns matching lines with file paths and line numbers. Respects .gitignore. Output is truncated to 100 matches or 50KB (whichever is hit first). Long lines are truncated to 500 chars."""
    flags = re.IGNORECASE if args.ignore_case else 0
    try:
        regex = re.compile(re.escape(args.pattern) if args.literal else args.pattern, flags)
    except re.error as exc:
        raise InvalidArguments(f"regex parse error: {exc}") from None
    limit = max(1, args.limit or DEFAULT_LIMIT)
    context = max(0, args.context or 0)

    workspace = ctx.workspace
    root = workspace.relpath(args.path)
    try:
        files = await workspace.files(args.path)
    except FileMissing:
        raise FileMissing(root or ".", kind="path") from None
    single = files == [root] and bool(root)
    every = files if root == "" else await _tree(ctx)
    ignore = await Ignore.load(every, workspace.read_bytes, root=root)
    globber = _globber(args.glob) if args.glob and not single else None

    def shown(rel: str) -> str:
        if single:
            return rel.rsplit("/", 1)[-1]
        return rel[len(root) + 1 :] if root else rel

    out: list[str] = []
    cut_lines = False
    matches = 0
    for rel in files:
        if not single and ignore.ignored(rel):
            continue
        name = shown(rel)
        if globber and not globber.match(name):
            continue
        data = await workspace.read_bytes(rel)
        if b"\x00" in data[:_BINARY_PROBE]:
            continue
        lines = data.decode("utf-8", errors="replace").replace("\r\n", "\n").split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        for number, line in enumerate(lines, 1):
            if not regex.search(line):
                continue
            matches += 1
            for at in range(max(1, number - context), min(len(lines), number + context) + 1):
                text, was_cut = truncate_line(lines[at - 1].replace("\r", ""))
                cut_lines |= was_cut
                out.append(f"{name}:{at}: {text}" if at == number else f"{name}-{at}- {text}")
            if matches >= limit:
                break
        if matches >= limit:
            break

    if not matches:
        return "No matches found"
    page = truncate_head("\n".join(out), max_lines=sys.maxsize)
    notes: list[str] = []
    if matches >= limit:
        notes.append(f"{limit} matches limit reached. Use limit={limit * 2} for more, or refine pattern")
    if page.truncated:
        notes.append(f"{format_size(DEFAULT_MAX_BYTES)} limit reached")
    if cut_lines:
        notes.append(f"Some lines truncated to {GREP_MAX_LINE_LENGTH} chars. Use read tool to see full lines")
    return page.content + (f"\n\n[{'. '.join(notes)}]" if notes else "")


async def _tree(ctx: ToolContext) -> list[str]:
    """Every file in the workspace (for the ``.gitignore`` files above a searched directory)."""
    try:
        return await ctx.workspace.files("")
    except FileMissing:
        return []
