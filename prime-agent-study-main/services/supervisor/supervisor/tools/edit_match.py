"""Finding and applying text edits the way pi's ``edit`` does.

* Line endings and a BOM are taken off before matching and put back after, so a
  one-line edit to a CRLF file stays a one-line change.
* Every edit is matched against the same original text. An exact match is tried
  first; failing that, a fuzzy one in a normalised view of the file (NFKC, trailing
  whitespace stripped per line, curly quotes, Unicode dashes and spaces made
  ASCII). Fuzzy replacements are made in that view and only the lines they touch
  are taken from it: every other line keeps its original bytes.
* An edit that is missing, ambiguous, overlaps another or changes nothing is
  refused with pi's wording, which tells the model how to fix the call.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass
from itertools import pairwise
from typing import Final

__all__ = ["BOM", "Edit", "EditError", "apply_edits", "normalize_for_fuzzy_match", "unified_diff"]

BOM: Final = "﻿"
MAX_DIFF_LINES: Final = 200

_SINGLE_QUOTES: Final = re.compile("[‘’‚‛]")
_DOUBLE_QUOTES: Final = re.compile("[“”„‟]")
_DASHES: Final = re.compile("[‐‑‒–—―−]")
_SPACES: Final = re.compile("[  -   　]")


class EditError(ValueError):
    """An edit that cannot be applied safely; the message says how to fix the call."""


@dataclass(frozen=True)
class Edit:
    old: str
    new: str
    replace_all: bool = False  # tool sets pinned before pi's schema could ask for it


def normalize_for_fuzzy_match(text: str) -> str:
    """pi's ``normalizeForFuzzyMatch``."""
    text = unicodedata.normalize("NFKC", text)
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = _SINGLE_QUOTES.sub("'", text)
    text = _DOUBLE_QUOTES.sub('"', text)
    text = _DASHES.sub("-", text)
    return _SPACES.sub(" ", text)


def _lf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _find(content: str, old: str) -> tuple[int, int, bool]:
    """(index, length, fuzzy) of ``old`` in ``content``; index -1 when absent."""
    at = content.find(old)
    if at != -1:
        return at, len(old), False
    fuzzy_old = normalize_for_fuzzy_match(old)
    at = normalize_for_fuzzy_match(content).find(fuzzy_old)
    return at, len(fuzzy_old), at != -1


def _count(content: str, old: str) -> int:
    return normalize_for_fuzzy_match(content).count(normalize_for_fuzzy_match(old))


def _spans(content: str) -> list[tuple[int, int]]:
    spans, offset = [], 0
    for line in content.splitlines(keepends=True):
        spans.append((offset, offset + len(line)))
        offset += len(line)
    return spans


def _replace(content: str, replacements: list[tuple[int, int, str]], offset: int = 0) -> str:
    out = content
    for start, length, new in sorted(replacements, reverse=True):
        at = start - offset
        out = out[:at] + new + out[at + length :]
    return out


@dataclass(slots=True)
class _Lines:
    """A run of lines ``[first, end)`` that some replacements touch."""

    first: int
    end: int
    replacements: list[tuple[int, int, str]]


def _overlay(original: str, base: str, replacements: list[tuple[int, int, str]]) -> str:
    """Replacements found in ``base`` (a normalised view of ``original``), applied so
    that only the lines they touch come from the view (pi's
    ``applyReplacementsPreservingUnchangedLines``)."""
    original_lines = original.splitlines(keepends=True)
    base_spans = _spans(base)
    if len(original_lines) != len(base_spans):
        raise EditError("Cannot preserve unchanged lines because the base content has a different line count.")
    groups: list[_Lines] = []
    for start, length, new in sorted(replacements):
        first = next(i for i, (a, b) in enumerate(base_spans) if a <= start < b or (start == b == base_spans[-1][1]))
        last = first
        while last < len(base_spans) and base_spans[last][1] < start + length:
            last += 1
        last = min(last, len(base_spans) - 1)
        if groups and first < groups[-1].end:
            groups[-1].end = max(groups[-1].end, last + 1)
            groups[-1].replacements.append((start, length, new))
        else:
            groups.append(_Lines(first, last + 1, [(start, length, new)]))
    out, line = "", 0
    for group in groups:
        out += "".join(original_lines[line : group.first])
        a, b = base_spans[group.first][0], base_spans[group.end - 1][1]
        out += _replace(base[a:b], group.replacements, a)
        line = group.end
    return out + "".join(original_lines[line:])


def apply_edits(original: str, edits: list[Edit], path: str) -> tuple[str, int]:
    """``original`` with every edit applied, and how many blocks were replaced.

    Raises EditError (nothing changed) when an edit is empty, missing, ambiguous,
    overlaps another, or changes nothing.
    """
    if not edits:
        raise EditError("Edit tool input is invalid. edits must contain at least one replacement.")
    bom = original.startswith(BOM)
    body = original[1:] if bom else original
    crlf = "\r\n" in body
    content = _lf(body)
    many = len(edits) > 1
    planned = [Edit(_lf(e.old), _lf(e.new), e.replace_all) for e in edits]
    for i, edit in enumerate(planned):
        if not edit.old:
            raise EditError(f"edits[{i}].oldText must not be empty in {path}." if many else f"oldText must not be empty in {path}.")
    fuzzy = any(_find(content, e.old)[2] for e in planned)
    base = normalize_for_fuzzy_match(content) if fuzzy else content
    found: list[tuple[int, int, str, int]] = []  # start, length, new, edit index
    for i, edit in enumerate(planned):
        old = normalize_for_fuzzy_match(edit.old) if fuzzy else edit.old
        at = base.find(old)
        if at == -1:
            raise EditError(
                f"Could not find edits[{i}] in {path}. The oldText must match exactly including all whitespace and newlines."
                if many
                else f"Could not find the exact text in {path}. The old text must match exactly including all whitespace and newlines."
            )
        occurrences = _count(base, old)
        if occurrences > 1 and not edit.replace_all:
            raise EditError(
                f"Found {occurrences} occurrences of edits[{i}] in {path}. Each oldText must be unique. Please provide more context to make it unique."
                if many
                else f"Found {occurrences} occurrences of the text in {path}. The text must be unique. Please provide more context to make it unique."
            )
        starts = [at]
        if edit.replace_all:
            starts, pos = [], base.find(old)
            while pos != -1:
                starts.append(pos)
                pos = base.find(old, pos + len(old))
        found += [(s, len(old), edit.new, i) for s in starts]
    found.sort()
    for (a, la, _, i), (b, _, _, j) in pairwise(found):
        if a + la > b:
            raise EditError(f"edits[{i}] and edits[{j}] overlap in {path}. Merge them into one edit or target disjoint regions.")
    replacements = [(s, n, new) for s, n, new, _ in found]
    updated = _overlay(content, base, replacements) if fuzzy else _replace(base, replacements)
    if updated == content:
        raise EditError(
            f"No changes made to {path}. The replacements produced identical content."
            if many
            else f"No changes made to {path}. The replacement produced identical content. This might indicate an issue with special characters or the text not existing as expected."
        )
    if crlf:
        updated = updated.replace("\n", "\r\n")
    return (BOM if bom else "") + updated, len(found)


def unified_diff(before: str, after: str, path: str) -> str:
    """The change as a unified diff, at most ``MAX_DIFF_LINES`` lines (for people, not the model)."""
    lines = list(
        difflib.unified_diff(
            _lf(before).splitlines(), _lf(after).splitlines(), f"a/{path}", f"b/{path}", lineterm="", n=2
        )
    )
    if len(lines) > MAX_DIFF_LINES:
        lines = [*lines[:MAX_DIFF_LINES], f"[... {len(lines) - MAX_DIFF_LINES} more diff lines]"]
    return "\n".join(lines)
