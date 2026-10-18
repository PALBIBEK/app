"""read -- file contents, with line numbers, pagination and image support.

Three kinds of file, decided by **content** rather than extension:

* **text** -- returned with absolute line numbers, capped at whichever of
  `MAX_LINES` or `MAX_BYTES` is hit first, with the offset to resume from.
  The resume hint is what turns truncation from a wall into a loop the model
  can drive; without it a 5,000-line file is simply unreadable past the cap.
* **image** -- returned as a real attachment block, so the model can see it.
* **anything else binary** -- reported as binary. Decoding it as UTF-8 with
  `errors="replace"` produces a screenful of U+FFFD *and* a success flag, so
  the model burns a turn on garbage it has no way to recognize as garbage.
"""

from __future__ import annotations

from typing import Any

from ..errors import SandboxViolation
from ..harness.env import FileErrorCode
from ..sandbox.paths import resolve_in_root
from ..types import ImageContent, TextContent
from .base import Tool, ToolContext, ToolResult, fail, ok
from .image import detect_supported_image_mime_type, encode_base64
from .path_utils import resolve_read_path

MAX_LINES = 2000
MAX_BYTES = 50 * 1024


def _looks_binary(payload: bytes) -> bool:
    """A NUL byte in the first block is the classic heuristic, and the same
    one `git` and `grep` use. Text files essentially never contain one."""
    return b"\x00" in payload[:8192]


def _number(lines: list[str], start: int) -> str:
    """Absolute line numbers. Restarting at 1 on every page would make them
    useless for locating anything, and actively misleading for `edit`."""
    return "\n".join(f"{start + offset:>6}\t{line}"
                     for offset, line in enumerate(lines))


def read_tool() -> Tool:
    def run(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        raw = args.get("path")
        if not raw:
            return fail("read requires a `path`.")
        try:
            path = resolve_in_root(str(raw), ctx.policy)
            path = resolve_in_root(str(resolve_read_path(path)), ctx.policy)
        except SandboxViolation as exc:
            return fail(str(exc))

        if path.is_dir():
            return fail(f"{raw} is a directory -- use `ls` to list it.")

        # Existence is the backend's question, not ours: a remote or container
        # env may answer it differently, and asking here would be a second
        # round trip whose answer could already be stale by the time the read
        # happens.
        result = ctx.env.read_binary_file(str(path))
        if not result.is_ok:
            if result.error.code is FileErrorCode.NOT_FOUND:
                return fail(f"no such file: {raw}")
            return fail(f"could not read {raw}: {result.error}")
        payload = result.value

        mime_type = detect_supported_image_mime_type(payload)
        if mime_type:
            return ToolResult(
                content=[TextContent(text=f"Read image file [{mime_type}]"),
                         ImageContent(data=encode_base64(payload), mime_type=mime_type)],
                details={"path": str(path), "mime_type": mime_type})

        if _looks_binary(payload):
            return ok(f"[binary file, {len(payload)} bytes -- not text and not a "
                      f"supported image format]", path=str(path), binary=True)

        text = payload.decode("utf-8", errors="replace")
        all_lines = text.splitlines()
        total = len(all_lines)

        offset = int(args.get("offset") or 1)
        if offset > total and total > 0:
            return fail(f"offset {offset} is beyond end of file "
                        f"({total} lines total)")
        start = max(1, offset)

        limit = args.get("limit")
        end = min(start - 1 + int(limit), total) if limit else total
        selected = all_lines[start - 1:end]

        # Two independent caps. Lines alone let one enormous line blow the
        # context; bytes alone let a file of 100k short lines do the same.
        if len(selected) > MAX_LINES:
            selected = selected[:MAX_LINES]

        # A single line over budget is not a pagination problem -- there is no
        # offset that would help. Truncating it would hand back a fragment with
        # no indication of where it was cut, so name the problem and the
        # command that gets the rest. Checked before the accumulate loop below,
        # which always keeps at least one line.
        if selected and len(selected[0].encode("utf-8")) > MAX_BYTES:
            size = len(selected[0].encode("utf-8"))
            return ok(f"[line {start} is {size} bytes, over the "
                      f"{MAX_BYTES // 1024}KB limit. Use bash: "
                      f"sed -n '{start}p' {raw} | head -c {MAX_BYTES}]",
                      path=str(path), truncated=True, total_lines=total)

        kept, budget = [], MAX_BYTES
        for line in selected:
            cost = len(line.encode("utf-8")) + 8      # + the line-number gutter
            if kept and cost > budget:
                break
            budget -= cost
            kept.append(line)

        selected = kept
        body = _number(selected, start)
        note = ""
        shown_to = start - 1 + len(selected)
        if shown_to < total:
            note = (f"\n\n[showing lines {start}-{shown_to} of {total}. "
                    f"Use offset={shown_to + 1} to continue.]")

        return ok(body + note, path=str(path), truncated=bool(note),
                  total_lines=total)

    return Tool(
        name="read",
        description=f"Read a file's contents. Supports text files and images "
                    f"(jpg, png, gif, webp, bmp); images are returned as "
                    f"attachments. Text output carries line numbers and is capped "
                    f"at {MAX_LINES} lines or {MAX_BYTES // 1024}KB, whichever "
                    f"comes first. Use offset/limit for large files; continue with "
                    f"the reported offset until complete.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "offset": {"type": "number",
                           "description": "line number to start from (1-indexed)"},
                "limit": {"type": "number",
                          "description": "maximum number of lines to read"},
            },
            "required": ["path"],
        },
        run=run,
    )
