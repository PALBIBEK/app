"""Bounding how much text reaches the model.

Every character a tool returns is re-sent on every subsequent turn for the
rest of the run, so an unbounded tool result is not a one-off cost -- it is
a tax on all remaining turns. Truncation is a budget mechanism, not tidiness.
"""

from __future__ import annotations


def truncate_text(text: str, limit: int) -> tuple[str, bool]:
    """Keep the head, drop the middle, keep the tail.

    Head-only truncation is the intuitive choice and the wrong one: a
    command's answer, a stack trace's exception line, and a file's closing
    section all live at the end. Keeping both ends preserves the two places
    information actually concentrates.
    """
    if limit <= 0 or len(text) <= limit:
        return text, len(text) > limit if limit > 0 else False
    half = limit // 2
    elided = len(text) - (half * 2)
    return f"{text[:half]}\n\n[... {elided} characters elided ...]\n\n{text[-half:]}", True


def truncate_lines(lines: list[str], limit: int) -> tuple[list[str], bool]:
    """Line-wise cap for list-shaped output (grep hits, directory entries)."""
    if limit <= 0 or len(lines) <= limit:
        return lines, False
    return lines[:limit], True
