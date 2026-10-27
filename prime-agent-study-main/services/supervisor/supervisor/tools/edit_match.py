"""Finding and applying text edits: normalise for matching, keep every other byte.

The rule the edit tool follows: **normalise to find, preserve the original everywhere
the edit did not touch.**

* Line endings and a BOM are taken off before matching and put back after, so a
  one-line edit to a CRLF file stays a one-line change instead of failing to match
  (the model writes ``\\n``) or rewriting every line ending.
* Typographic look-alikes match their ASCII forms (curly quotes, non-breaking spaces,
  dashes), one character for one character, so the model's straight apostrophe finds
  the file's curly one, and the replacement lands at exactly the matched span.
* Several edits are all located against the same original text, refused if any is
  missing, ambiguous or overlaps another, and applied back to front so earlier
  offsets stay valid. A wrong edit is worse than a refused one: the model recovers
  from an error message, but cannot see corruption it was told succeeded.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import Final

__all__ = ["BOM", "Edit", "EditError", "apply_edits", "unified_diff"]

BOM: Final = "﻿"
MAX_DIFF_LINES: Final = 200

_LOOKALIKES: Final = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "‚": "'",
        "‛": "'",
        "“": '"',
        "”": '"',
        "„": '"',
        "‟": '"',
        " ": " ",
        " ": " ",
        " ": " ",
        "‐": "-",
        "‑": "-",
        "‒": "-",
        "–": "-",
        "—": "-",
        "−": "-",
    }
)
"""One character to one character, so a match's offsets are the same in the original."""


class EditError(ValueError):
    """An edit that cannot be applied safely; the message says how to fix the call."""


@dataclass(frozen=True)
class Edit:
    old: str
    new: str
    replace_all: bool = False


def _lf(text: str) -> str:
    return text.replace("\r\n", "\n")


def _spans(haystack: str, needle: str) -> list[int]:
    found: list[int] = []
    at = haystack.find(needle)
    while at != -1:
        found.append(at)
        at = haystack.find(needle, at + len(needle))
    return found


def _locate(content: str, folded: str, edit: Edit, path: str) -> list[tuple[int, int]]:
    old = _lf(edit.old)
    spans = _spans(content, old)
    if not spans:  # exact first; then the look-alike fold, same offsets
        spans = _spans(folded, old.translate(_LOOKALIKES))
    if not spans:
        raise EditError(f"old_text does not occur in {path}; read the file and copy the text exactly")
    if len(spans) > 1 and not edit.replace_all:
        raise EditError(
            f"old_text occurs {len(spans)} times in {path}; include more context or set replace_all"
        )
    return [(at, at + len(old)) for at in spans]


def apply_edits(original: str, edits: list[Edit], path: str) -> tuple[str, int]:
    """``original`` with every edit applied, and how many replacements were made.

    Raises EditError (nothing changed) when an edit is empty, missing, ambiguous,
    overlaps another, or changes nothing.
    """
    if not edits:
        raise EditError("no edit given: pass old_text and new_text, or edits")
    bom = original.startswith(BOM)
    body = original[1:] if bom else original
    crlf = "\r\n" in body
    content = _lf(body)
    folded = content.translate(_LOOKALIKES)
    planned: list[tuple[int, int, str]] = []
    for edit in edits:
        if not edit.old:
            raise EditError("old_text must not be empty")
        if _lf(edit.old) == _lf(edit.new):
            raise EditError("old_text and new_text are the same; the edit would change nothing")
        planned += [(a, b, _lf(edit.new)) for a, b in _locate(content, folded, edit, path)]
    planned.sort()
    for (_, end, _), (start, _, _) in zip(planned, planned[1:], strict=False):
        if start < end:
            raise EditError("two edits overlap; merge them into one edit")
    updated = content
    for start, end, new in reversed(planned):
        updated = updated[:start] + new + updated[end:]
    if crlf:
        updated = updated.replace("\n", "\r\n")
    return (BOM if bom else "") + updated, len(planned)


def unified_diff(before: str, after: str, path: str) -> str:
    """The change as a unified diff, at most ``MAX_DIFF_LINES`` lines."""
    lines = list(
        difflib.unified_diff(
            _lf(before).splitlines(), _lf(after).splitlines(), f"a/{path}", f"b/{path}", lineterm="", n=2
        )
    )
    if len(lines) > MAX_DIFF_LINES:
        lines = [*lines[:MAX_DIFF_LINES], f"[... {len(lines) - MAX_DIFF_LINES} more diff lines]"]
    return "\n".join(lines)
