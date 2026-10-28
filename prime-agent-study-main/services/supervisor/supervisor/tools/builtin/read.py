"""read: a file's contents, a page at a time (as pi's read tool returns them).

Three kinds of file, told apart by content rather than by name:

* **text** comes back as it is, at most ``MAX_LINES`` lines or ``MAX_BYTES`` per
  call, ending with which lines were shown and the offset to continue from. The
  resume hint is what turns the cap from a wall into a loop the model can drive:
  without it a 5,000-line file is unreadable past the cap. Lines are not numbered:
  a numbered line invites the model to copy the number into an edit's old text.
  A line longer than the whole page is not cut: the model is told how to read it
  with bash (pi's notes, word for word).
* **images** are reported as images (kind and size), not decoded as text.
* **anything else binary** is reported as binary. Decoding it as UTF-8 would hand the
  model a screen of replacement characters marked as a success, which it has no way
  to recognise as garbage.
"""

from __future__ import annotations

from typing import Final

from pydantic import Field

from supervisor.tools.args import ToolArgs, path_field
from supervisor.tools.context import ToolContext
from supervisor.tools.errors import InvalidArguments
from supervisor.tools.registry import tool
from supervisor.tools.spec import ToolClass
from supervisor.tools.truncate import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_LINES,
    format_size,
    truncate_head,
)

MAX_LINES: Final = DEFAULT_MAX_LINES
MAX_BYTES: Final = DEFAULT_MAX_BYTES

_IMAGES: Final = (
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpeg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
)


def _image_kind(data: bytes) -> str | None:
    for magic, kind in _IMAGES:
        if data.startswith(magic):
            return kind
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


class ReadArgs(ToolArgs):
    file_path: str = path_field("path", "file_path")
    """Path to the file to read (relative or absolute)"""

    offset: int | None = Field(default=None, ge=1)
    """Line number to start reading from (1-indexed)"""

    limit: int | None = Field(default=None, ge=1)
    """Maximum number of lines to read"""


@tool(tool_class=ToolClass.PURE)
async def read(args: ReadArgs, ctx: ToolContext) -> str:
    """Read the contents of a file. Output is truncated to 2000 lines or 50KB (whichever is hit first). Use offset/limit for large files. When you need the full file, continue with offset until complete.

    Binary files and images are reported, not decoded.
    """
    workspace = ctx.workspace
    rel = workspace.relpath(args.file_path)
    data = await workspace.read_bytes(rel)
    if (kind := _image_kind(data)) is not None:
        return f"{rel} is a {kind} image ({len(data)} bytes); it cannot be shown as text."
    if b"\x00" in data[:8192]:
        return f"{rel} is a binary file ({len(data)} bytes); it cannot be shown as text."
    text = data.decode("utf-8", errors="replace")
    if not text:
        return f"{rel} is empty."
    rows = text.split("\n")
    ends_with_newline = len(rows) > 1 and rows[-1] == ""
    if ends_with_newline:
        rows.pop()  # a trailing newline ends the last line; it does not start one
    total = len(rows)
    start = (args.offset or 1) - 1
    if start >= total:
        raise InvalidArguments(f"Offset {args.offset} is beyond end of file ({total} lines total)")
    stop = min(total, start + args.limit) if args.limit else total
    page = truncate_head("\n".join(rows[start:stop]))
    first = start + 1
    if page.truncated and page.output_lines == 0:
        size = format_size(len(rows[start].encode("utf-8")))
        return (
            f"[Line {first} is {size}, exceeds {format_size(MAX_BYTES)} limit. "
            f"Use bash: sed -n '{first}p' {rel} | head -c {MAX_BYTES}]"
        )
    last = start + page.output_lines if page.truncated else stop
    if page.truncated:
        limit = "" if page.truncated_by == "lines" else f" ({format_size(MAX_BYTES)} limit)"
        return f"{page.content}\n\n[Showing lines {first}-{last} of {total}{limit}. Use offset={last + 1} to continue.]"
    if last < total:
        return f"{page.content}\n\n[{total - last} more lines in file. Use offset={last + 1} to continue.]"
    return page.content + ("\n" if ends_with_newline else "")
