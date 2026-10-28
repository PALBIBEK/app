"""find: files by glob pattern, as pi's find (fd) returns them.

One path per line, relative to the directory searched, ``/`` after a directory; a
pattern without ``/`` matches names at any depth, one with ``/`` matches the path below
the directory searched (``**`` crossing directories). ``.gitignore`` is respected and
dotfiles are found (fd ``--hidden``); at most ``limit`` results and 50 KB, with a
closing note naming each limit hit.
"""

from __future__ import annotations

import sys
from typing import Final

from supervisor.tools.args import ToolArgs, path_field
from supervisor.tools.context import ToolContext
from supervisor.tools.errors import FileMissing, InvalidArguments
from supervisor.tools.ignore import Ignore, glob_regex
from supervisor.tools.registry import tool
from supervisor.tools.spec import ToolClass
from supervisor.tools.truncate import DEFAULT_MAX_BYTES, format_size, truncate_head

DEFAULT_LIMIT: Final = 1000


class FindArgs(ToolArgs):
    pattern: str
    """Glob pattern to match files, e.g. '*.ts', '**/*.json', or 'src/**/*.spec.ts'"""

    path: str = path_field("path", "file_path", default="")
    """Directory to search in (default: current directory)"""

    limit: int | None = None
    """Maximum number of results (default: 1000)"""


@tool(tool_class=ToolClass.PURE)
async def find(args: FindArgs, ctx: ToolContext) -> str:
    """Search for files by glob pattern. Returns matching file paths relative to the search directory. Respects .gitignore. Output is truncated to 1000 results or 50KB (whichever is hit first)."""
    pattern = args.pattern.strip()
    if not pattern:
        raise InvalidArguments("pattern must not be empty")
    workspace = ctx.workspace
    root = workspace.relpath(args.path)
    try:
        files = await workspace.files(args.path)
    except FileMissing:
        raise FileMissing(root or ".", kind="path") from None
    if root and files == [root]:
        raise InvalidArguments(f"Not a directory: {root}")
    every = files if not root else await workspace.files("")
    ignore = await Ignore.load(every, workspace.read_bytes, root=root)
    full_path = "/" in pattern
    if full_path and not pattern.startswith(("/", "**/")) and pattern != "**":
        pattern = f"**/{pattern}"  # as pi: a relative path pattern matches at any depth
    regex = glob_regex(pattern.lstrip("/"))
    limit = max(1, args.limit or DEFAULT_LIMIT)

    found: list[str] = []
    for rel, is_dir in _walk(files, root, ignore):
        below = rel[len(root) + 1 :] if root else rel
        subject = below if full_path else below.rsplit("/", 1)[-1]
        if regex.match(subject):
            found.append(f"{below}/" if is_dir else below)
            if len(found) >= limit:
                break
    if not found:
        return "No files found matching pattern"
    page = truncate_head("\n".join(found), max_lines=sys.maxsize)
    notes: list[str] = []
    if len(found) >= limit:
        notes.append(f"{limit} results limit reached. Use limit={limit * 2} for more, or refine pattern")
    if page.truncated:
        notes.append(f"{format_size(DEFAULT_MAX_BYTES)} limit reached")
    return page.content + (f"\n\n[{'. '.join(notes)}]" if notes else "")


def _walk(files: list[str], root: str, ignore: Ignore) -> list[tuple[str, bool]]:
    """Every file and directory below ``root`` that is not ignored, sorted by path."""
    seen: set[str] = set()
    out: list[tuple[str, bool]] = []
    for rel in files:
        if ignore.ignored(rel):
            continue
        out.append((rel, False))
        parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
        while parent and parent != root and parent not in seen:
            seen.add(parent)
            out.append((parent, True))
            parent = parent.rsplit("/", 1)[0] if "/" in parent else ""
    return sorted(out)
