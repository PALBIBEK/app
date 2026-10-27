"""read: a file's contents, a page at a time (as pi's read tool returns them).

Three kinds of file, told apart by content rather than by name:

* **text** comes back as it is, at most ``MAX_LINES`` lines or ``MAX_BYTES`` per
  call, ending with which lines were shown and the offset to continue from. The
  resume hint is what turns the cap from a wall into a loop the model can drive:
  without it a 5,000-line file is unreadable past the cap. Lines are not numbered:
  a numbered line invites the model to copy the number into an edit's old text.
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

MAX_LINES: Final = 2000
MAX_BYTES: Final = 50 * 1024
MAX_LINE_CHARS: Final = 2000
"""A longer line (minified code, a data blob) is cut: one line must not fill the page."""

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


def _clip(line: str) -> str:
    if len(line) > MAX_LINE_CHARS:
        end = "\n" if line.endswith("\n") else ""
        return f"{line[:MAX_LINE_CHARS]} [... line cut at {MAX_LINE_CHARS} characters]{end}"
    return line


class ReadArgs(ToolArgs):
    file_path: str = path_field("file_path", "path")
    """Path to file (absolute under workspace, or relative)"""

    offset: int | None = Field(default=None, ge=1)
    """1-indexed line to start from (optional)"""

    limit: int | None = Field(default=None, ge=1)
    """Most lines to return (optional; at most 2000)"""


@tool(tool_class=ToolClass.PURE)
async def read(args: ReadArgs, ctx: ToolContext) -> str:
    """Read the contents of a file. Output is truncated to 2000 lines or 50KB (whichever is hit first); use offset/limit for large files, and when you need the full file, continue with offset until complete.

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
    lines = text.splitlines(keepends=True)
    total = len(lines)
    start = (args.offset or 1) - 1
    if start >= total:
        raise InvalidArguments(f"offset {args.offset} is past the end of {rel}, which has {total} lines")
    stop = min(total, start + min(args.limit or MAX_LINES, MAX_LINES))
    page: list[str] = []
    size = 0
    capped = stop - start == MAX_LINES and stop < total
    for index in range(start, stop):
        line = _clip(lines[index])
        if page and size + len(line) > MAX_BYTES:
            capped = True
            break
        page.append(line)
        size += len(line)
    shown_to = start + len(page)
    body = "".join(page)  # the text exactly, its line endings included
    if shown_to < total:
        body = body.rstrip("\n")
        if capped or args.limit is None:
            body += f"\n\n[Showing lines {start + 1}-{shown_to} of {total}. Use offset={shown_to + 1} to continue.]"
        else:
            body += f"\n\n[{total - shown_to} more lines in file. Use offset={shown_to + 1} to continue.]"
    return body
