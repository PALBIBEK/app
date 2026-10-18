"""ls and find -- path enumeration.

Two tools, one module: they answer the same kind of question (what is on
disk) and share the same confinement logic, but stay separate on the wire
because that is the surface pi exposes and the two have genuinely different
shapes -- one takes a directory, the other a glob.
"""

from __future__ import annotations

from typing import Any

from ..errors import SandboxViolation
from ..harness.env import FileErrorCode
from ..sandbox.paths import resolve_in_root
from .base import Tool, ToolContext, ToolResult, fail, ok

MAX_ENTRIES = 500


def ls_tool() -> Tool:
    def run(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        raw = str(args.get("path") or ".")
        try:
            path = resolve_in_root(raw, ctx.policy)
        except SandboxViolation as exc:
            return fail(str(exc))

        listed = ctx.env.list_dir(str(path))
        if not listed.is_ok:
            if listed.error.code is FileErrorCode.NOT_DIRECTORY:
                return fail(f"{raw} is a file -- use `read` for its contents.")
            if listed.error.code is FileErrorCode.NOT_FOUND:
                return fail(f"no such directory: {raw}")
            return fail(f"could not list {raw}: {listed.error}")

        entries = sorted(listed.value,
                         key=lambda e: (e.kind != "directory", e.name))
        shown = entries[:MAX_ENTRIES]
        # Trailing slash on directories: without it the model cannot tell a
        # file from a directory and guesses which tool to use next.
        lines = [f"{e.name}/" if e.kind == "directory" else e.name for e in shown]
        if len(entries) > MAX_ENTRIES:
            lines.append(f"[{len(entries) - MAX_ENTRIES} more entries not shown]")
        return ok("\n".join(lines) or "[empty directory]", count=len(entries))

    return Tool(
        name="ls",
        description="List the entries of a directory. Directories are shown with a "
                    "trailing slash.",
        parameters={"type": "object", "properties": {"path": {"type": "string"}}},
        run=run,
    )


def find_tool() -> Tool:
    def run(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        pattern = str(args.get("pattern") or "")
        if not pattern:
            return fail("find requires a `pattern`, e.g. '**/*.py'.")

        base = ctx.policy.root
        if args.get("path"):
            try:
                base = resolve_in_root(str(args["path"]), ctx.policy)
            except SandboxViolation as exc:
                return fail(str(exc))

        try:
            matches = sorted(base.glob(pattern))
        except (ValueError, OSError) as exc:
            return fail(f"invalid pattern {pattern!r}: {exc}")

        # Anything a symlink drags outside the root is dropped rather than
        # reported: `find` is the natural way to *discover* such a link, and
        # listing it would hand back host paths the other tools then refuse.
        inside = []
        for m in matches:
            try:
                inside.append(m.resolve().relative_to(ctx.policy.root))
            except ValueError:
                continue

        if not inside:
            return ok(f"no matches for {pattern!r}")

        shown = inside[:MAX_ENTRIES]
        lines = [str(p) for p in shown]
        if len(inside) > MAX_ENTRIES:
            lines.append(f"[{len(inside) - MAX_ENTRIES} more matches not shown]")
        return ok("\n".join(lines), count=len(inside))

    return Tool(
        name="find",
        description="Find files by glob pattern (e.g. '**/*.py'). Returns paths "
                    "relative to the working directory.",
        parameters={
            "type": "object",
            "properties": {"pattern": {"type": "string"}, "path": {"type": "string"}},
            "required": ["pattern"],
        },
        run=run,
    )
