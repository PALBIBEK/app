from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Final

from supervisor.tools.args import ToolArgs, path_field
from supervisor.tools.context import ToolContext
from supervisor.tools.errors import InvalidArguments
from supervisor.tools.registry import tool
from supervisor.tools.spec import ToolClass

DEFAULT_MAX_MATCHES: Final = 100
_MATCH_CEILING: Final = 1_000
_MAX_FILE_BYTES: Final = 1_000_000
_MAX_LINE_CHARS: Final = 400


class GrepArgs(ToolArgs):
    pattern: str
    """Regular expression to search for"""

    path: str = path_field("path", "file_path", default="")
    """File or directory to search (default: workspace root)"""

    glob: str | None = None
    """Only search files matching this pattern, e.g. '*.py'"""

    case_insensitive: bool = False
    """Ignore case (default: false)"""

    max_matches: int | None = None
    """Stop after this many matching lines (default: 100)"""


@tool(tool_class=ToolClass.PURE)
async def grep(args: GrepArgs, ctx: ToolContext) -> list[str]:
    """Search files in the workspace for a regex/string pattern. Returns 'path:line:text' for each matching line.

    Binary and very large files are skipped, long lines are cut, and the match
    count is capped, because every line returned is spent from the model's
    context window.
    """
    try:
        regex = re.compile(args.pattern, re.IGNORECASE if args.case_insensitive else 0)
    except re.error as exc:
        raise InvalidArguments(f"invalid regular expression: {exc}") from None
    limit = min(max(args.max_matches or DEFAULT_MAX_MATCHES, 1), _MATCH_CEILING)

    workspace = ctx.workspace
    matches: list[str] = []
    for rel in await workspace.files(args.path):
        if args.glob and not PurePosixPath(rel).match(args.glob):
            continue
        data = await workspace.read_bytes(rel)
        if len(data) > _MAX_FILE_BYTES or b"\x00" in data[:8192]:
            continue
        for number, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
            if regex.search(line):
                matches.append(f"{rel}:{number}:{line[:_MAX_LINE_CHARS]}")
                if len(matches) == limit:
                    matches.append(f"[stopped after {limit} matches; narrow the pattern or path]")
                    return matches
    return matches
