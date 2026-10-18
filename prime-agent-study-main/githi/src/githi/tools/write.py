"""write -- create or overwrite a file."""

from __future__ import annotations

from typing import Any

from ..errors import SandboxViolation
from ..sandbox.paths import ensure_writable, resolve_in_root
from .base import Tool, ToolContext, ToolResult, fail, ok


def write_tool() -> Tool:
    def run(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        raw = args.get("path")
        if not raw:
            return fail("write requires a `path`.")
        content = args.get("content")
        if content is None:
            return fail("write requires `content` (use \"\" for an empty file).")

        try:
            path = resolve_in_root(str(raw), ctx.policy)
            ensure_writable(path, ctx.policy)
        except SandboxViolation as exc:
            return fail(str(exc))

        if path.is_dir():
            return fail(f"{raw} is a directory.")

        # `write_file` creates parent directories itself, so the backend gets
        # one round trip instead of two -- and a remote env is not asked to
        # mkdir a path it may express differently.
        result = ctx.env.write_file(str(path), str(content))
        if not result.is_ok:
            return fail(f"could not write {raw}: {result.error}")

        return ok(f"wrote {len(str(content))} characters to {raw}", path=str(path))

    return Tool(
        name="write",
        description="Write text to a file, creating parent directories as needed. "
                    "Overwrites the file if it exists -- use `edit` to change part "
                    "of a file you want to keep.",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
        run=run,
    )
