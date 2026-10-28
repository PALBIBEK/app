"""pi's output limits, so a tool's result reads (and costs) as pi's does.

2000 lines or 50 KB, whichever comes first. A command's output keeps its tail (the
answer and the last error are at the end); a listing or search keeps its head.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

__all__ = [
    "DEFAULT_MAX_BYTES",
    "DEFAULT_MAX_LINES",
    "GREP_MAX_LINE_LENGTH",
    "Truncation",
    "format_size",
    "truncate_head",
    "truncate_line",
    "truncate_tail",
]

DEFAULT_MAX_LINES: Final = 2000
DEFAULT_MAX_BYTES: Final = 50 * 1024
GREP_MAX_LINE_LENGTH: Final = 500


@dataclass(frozen=True, slots=True)
class Truncation:
    content: str
    truncated: bool
    truncated_by: str | None  # "lines" | "bytes" | None
    total_lines: int
    output_lines: int
    output_bytes: int
    last_line_partial: bool = False


def format_size(size: int) -> str:
    """pi's size wording: 512B, 50.0KB, 1.2MB."""
    if size < 1024:
        return f"{size}B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f}KB"
    return f"{size / (1024 * 1024):.1f}MB"


def _lines(content: str) -> list[str]:
    rows = content.split("\n")
    if len(rows) > 1 and rows[-1] == "":
        rows = rows[:-1]  # a trailing newline ends the last line; it does not start one
    return rows


def _size(text: str) -> int:
    return len(text.encode("utf-8"))


def truncate_tail(content: str, *, max_lines: int = DEFAULT_MAX_LINES, max_bytes: int = DEFAULT_MAX_BYTES) -> Truncation:
    """The last ``max_lines`` lines within ``max_bytes`` (pi's ``truncateTail``)."""
    rows = _lines(content)
    if len(rows) <= max_lines and _size(content) <= max_bytes:
        return Truncation(content, False, None, len(rows), len(rows), _size(content))
    kept: list[str] = []
    used, by, partial = 0, "lines", False
    for row in reversed(rows):
        if len(kept) >= max_lines:
            break
        cost = _size(row) + (1 if kept else 0)
        if used + cost > max_bytes:
            by = "bytes"
            if not kept:  # one line longer than the limit: its end
                tail = row.encode("utf-8")[-max_bytes:].decode("utf-8", errors="ignore")
                kept.append(tail)
                used, partial = _size(tail), True
            break
        kept.append(row)
        used += cost
    kept.reverse()
    out = "\n".join(kept)
    return Truncation(out, True, by, len(rows), len(kept), _size(out), partial)


def truncate_head(content: str, *, max_lines: int = DEFAULT_MAX_LINES, max_bytes: int = DEFAULT_MAX_BYTES) -> Truncation:
    """The first ``max_lines`` lines within ``max_bytes`` (pi's ``truncateHead``)."""
    rows = _lines(content)
    if len(rows) <= max_lines and _size(content) <= max_bytes:
        return Truncation(content, False, None, len(rows), len(rows), _size(content))
    kept: list[str] = []
    used, by = 0, "lines"
    for row in rows[:max_lines]:
        cost = _size(row) + (1 if kept else 0)
        if used + cost > max_bytes:
            by = "bytes"
            break
        kept.append(row)
        used += cost
    out = "\n".join(kept)
    return Truncation(out, True, by, len(rows), len(kept), _size(out))


def truncate_line(line: str, max_chars: int = GREP_MAX_LINE_LENGTH) -> tuple[str, bool]:
    if len(line) <= max_chars:
        return line, False
    return f"{line[:max_chars]}... [truncated]", True
