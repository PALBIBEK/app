"""grep -- regex search over sandboxed files.

Implemented in Python rather than shelling out to `grep`/`rg`, for two
reasons. Behaviour would otherwise depend on which binaries the host happens
to have and which flags that version supports -- a tool whose output format
shifts between machines is one the model cannot learn. And it stays available
even when the shell is unavailable or the policy forbids it.

Three output modes with genuinely different costs, mirroring the split pi
uses: knowing *which files* match is far cheaper than pulling every matching
line into context, and the model should be able to pay only for what it needs.
"""

from __future__ import annotations

import re
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from ..errors import SandboxViolation
from ..sandbox.paths import resolve_in_root
from .base import Tool, ToolContext, ToolResult, fail, ok

DEFAULT_HEAD_LIMIT = 100
MAX_FILE_BYTES = 2_000_000
_MODES = ("files_with_matches", "content", "count")


def grep_tool() -> Tool:
    def run(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        pattern = str(args.get("pattern") or "")
        if not pattern:
            return fail("grep requires a `pattern`.")

        mode = str(args.get("output_mode") or "files_with_matches")
        if mode not in _MODES:
            return fail(f"output_mode must be one of {', '.join(_MODES)}; got {mode!r}.")

        flags = re.IGNORECASE if args.get("case_insensitive") else 0
        try:
            regex = re.compile(pattern, flags)
        except re.error as exc:
            return fail(f"invalid regex {pattern!r}: {exc}")

        try:
            files = _candidates(args, ctx)
        except SandboxViolation as exc:
            return fail(str(exc))
        if isinstance(files, ToolResult):
            return files

        head_limit = int(args.get("head_limit") or DEFAULT_HEAD_LIMIT)
        hits: list[str] = []
        counts: dict[str, int] = {}
        capped = False

        for path in files:
            text = _read_text(path, ctx.env)
            if text is None:
                continue                      # binary or unreadable: skip, never fatal
            rel = _rel(path, ctx)
            n = 0
            for lineno, line in enumerate(text.splitlines(), start=1):
                if not regex.search(line):
                    continue
                n += 1
                if mode == "content":
                    if len(hits) >= head_limit:
                        capped = True
                        break
                    hits.append(f"{rel}:{lineno}:{line.rstrip()[:400]}")
            if n:
                counts[rel] = n
            if capped:
                break

        if not counts:
            return ok(f"no matches for {pattern!r}")

        if mode == "files_with_matches":
            lines = list(counts)
        elif mode == "count":
            lines = [f"{f}: {c}" for f, c in counts.items()]
        else:
            lines = hits

        if len(lines) > head_limit:
            lines, capped = lines[:head_limit], True
        body = "\n".join(lines)
        if capped:
            body += (f"\n[showing first {head_limit}; more matches exist -- "
                     "narrow the pattern or raise head_limit]")
        return ok(body, files_matched=len(counts))

    return Tool(
        name="grep",
        description="Search files with a regular expression. output_mode: "
                    "'files_with_matches' (default, cheapest -- just which files "
                    "match), 'count' (matches per file), or 'content' (the matching "
                    "lines with line numbers). Narrow with `glob` or `path`.",
        parameters={
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "path": {"type": "string", "description": "a single file to search"},
                "glob": {"type": "string", "description": "e.g. '**/*.py'"},
                "output_mode": {"type": "string", "enum": list(_MODES)},
                "case_insensitive": {"type": "boolean"},
                "head_limit": {"type": "integer"},
            },
            "required": ["pattern"],
        },
        run=run,
    )


def _candidates(args: dict[str, Any], ctx: ToolContext) -> list[Path] | ToolResult:
    if args.get("path"):
        path = resolve_in_root(str(args["path"]), ctx.policy)
        if not path.is_file():
            return fail(f"no such file: {args['path']}")
        return [path]

    glob = str(args.get("glob") or "**/*")
    ignored = _ignored_patterns(ctx.policy.root)
    return [p for p in sorted(ctx.policy.root.glob(glob)) if p.is_file()
            and not _is_ignored(p, ctx.policy.root, ignored)]


def _ignored_patterns(root: Path) -> list[str]:
    path = root / ".gitignore"
    if not path.is_file():
        return []
    return [line.strip() for line in path.read_text(errors="ignore").splitlines()
            if line.strip() and not line.lstrip().startswith("#")]


def _is_ignored(path: Path, root: Path, patterns: list[str]) -> bool:
    relative = str(path.relative_to(root))
    return any(fnmatch(relative, pattern) or fnmatch(path.name, pattern.rstrip("/"))
               for pattern in patterns)


def _read_text(path: Path, env) -> str | None:
    """None means "skip this file", never "fail the search".

    One unreadable file among a thousand is normal -- a broken symlink, a
    permission the process lacks -- and aborting the whole grep over it would
    turn a partial answer into no answer.
    """
    result = env.read_binary_file(str(path))
    if not result.is_ok:
        return None
    raw = result.value
    if len(raw) > MAX_FILE_BYTES:
        return None
    # A NUL byte is the conventional binary sniff. Searching a binary yields
    # nothing useful and can emit control characters into the transcript.
    if b"\x00" in raw[:8000]:
        return None
    return raw.decode("utf-8", errors="replace")


def _rel(path: Path, ctx: ToolContext) -> str:
    try:
        return str(path.relative_to(ctx.policy.root))
    except ValueError:                       # pragma: no cover - defensive
        return str(path)
