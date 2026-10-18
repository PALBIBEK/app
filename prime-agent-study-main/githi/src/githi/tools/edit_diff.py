"""Edit matching and diff generation (port of `harness/tools/edit-diff.ts`).

The governing rule is one sentence: **normalize for matching, preserve the
original bytes everywhere the edit did not touch.**

Every piece here follows from it. Line endings and the BOM are stripped before
matching and restored after, so a one-line edit to a CRLF file stays a one-line
diff instead of rewriting all 2,000 line endings. Fuzzy normalization lets the
model's ASCII apostrophe find the file's curly one -- but the replacement is
then overlaid line-by-line, so the curly apostrophes on *other* lines survive.

Batched edits are all located against the same original content and applied
back to front, because applying front to back would shift the offsets of every
edit after the first. Anything ambiguous -- text appearing twice, two edits
touching the same span, a replacement that changes nothing -- raises rather
than guesses. A wrong edit is worse than a refused one: the model can recover
from an error message, but it cannot see corruption it was told succeeded.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass

BOM = "﻿"

_SMART_SINGLE = re.compile(r"[‘’‚‛]")
_SMART_DOUBLE = re.compile(r"[“”„‟]")
_DASHES = re.compile(r"[‐‑‒–—―−]")
_SPACES = re.compile(r"[  -   　]")


# --------------------------------------------------------------------------
# line endings and BOM
# --------------------------------------------------------------------------


def detect_line_ending(content: str) -> str:
    """Whichever ending appears first wins.

    A file that is mostly LF with one stray CRLF is an LF file; rewriting it
    as CRLF because a lone `\\r\\n` existed somewhere would touch every line.
    """
    lf = content.find("\n")
    crlf = content.find("\r\n")
    if lf == -1 or crlf == -1:
        return "\n"
    return "\r\n" if crlf < lf else "\n"


def normalize_to_lf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def restore_line_endings(text: str, ending: str) -> str:
    return text.replace("\n", "\r\n") if ending == "\r\n" else text


def strip_bom(content: str) -> tuple[str, str]:
    """Return `(bom, text)`. Kept separate so the caller can write it back --
    dropping a BOM silently changes how other tools read the file."""
    return (BOM, content[1:]) if content.startswith(BOM) else ("", content)


# --------------------------------------------------------------------------
# fuzzy matching
# --------------------------------------------------------------------------


def normalize_for_fuzzy_match(text: str) -> str:
    """Fold the differences that come from text passing through a model.

    Trailing whitespace, smart quotes, Unicode dashes and exotic spaces all
    survive a copy-paste through an LLM in altered form. Matching on the
    normalized text means the model does not have to reproduce a character it
    may not even be able to distinguish in its own output.
    """
    text = unicodedata.normalize("NFKC", text)
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = _SMART_SINGLE.sub("'", text)
    text = _SMART_DOUBLE.sub('"', text)
    text = _DASHES.sub("-", text)
    return _SPACES.sub(" ", text)


@dataclass
class FuzzyMatchResult:
    found: bool
    index: int
    match_length: int
    used_fuzzy_match: bool
    content_for_replacement: str
    """The content the index refers to. On a fuzzy match this is the
    *normalized* content, because normalization shifts offsets -- replacing
    against the original using a normalized index would land in the wrong
    place."""


def fuzzy_find_text(content: str, old_text: str) -> FuzzyMatchResult:
    exact = content.find(old_text)
    if exact != -1:
        return FuzzyMatchResult(True, exact, len(old_text), False, content)

    fuzzy_content = normalize_for_fuzzy_match(content)
    fuzzy_old = normalize_for_fuzzy_match(old_text)
    index = fuzzy_content.find(fuzzy_old)
    if index == -1:
        return FuzzyMatchResult(False, -1, 0, False, content)
    return FuzzyMatchResult(True, index, len(fuzzy_old), True, fuzzy_content)


# --------------------------------------------------------------------------
# applying edits
# --------------------------------------------------------------------------


@dataclass
class Edit:
    old_text: str
    new_text: str


@dataclass
class AppliedEditsResult:
    base_content: str
    new_content: str


@dataclass
class _Matched:
    edit_index: int
    match_index: int
    match_length: int
    new_text: str


def _split_lines_keeping_endings(content: str) -> list[str]:
    return re.findall(r"[^\n]*\n|[^\n]+", content)


def _line_spans(content: str) -> list[tuple[int, int]]:
    spans, offset = [], 0
    for line in _split_lines_keeping_endings(content):
        spans.append((offset, offset + len(line)))
        offset += len(line)
    return spans


def _apply(content: str, replacements: list[_Matched], offset: int = 0) -> str:
    """Back to front, so each replacement's index stays valid: applying front
    to back would shift every span after the first by the length delta."""
    result = content
    for replacement in reversed(replacements):
        start = replacement.match_index - offset
        result = (result[:start] + replacement.new_text
                  + result[start + replacement.match_length:])
    return result


def _replacement_line_range(spans: list[tuple[int, int]],
                            replacement: _Matched) -> tuple[int, int]:
    start_offset = replacement.match_index
    end_offset = replacement.match_index + replacement.match_length

    start_line = next((i for i, (s, e) in enumerate(spans)
                       if s <= start_offset < e), -1)
    if start_line == -1:
        raise ValueError("Replacement range is outside the base content.")

    end_line = start_line
    while end_line < len(spans) and spans[end_line][1] < end_offset:
        end_line += 1
    if end_line >= len(spans):
        raise ValueError("Replacement range is outside the base content.")
    return start_line, end_line + 1


def apply_replacements_preserving_unchanged_lines(
    original_content: str, base_content: str, replacements: list[_Matched],
) -> str:
    """Overlay normalized-space edits onto the original, line block by line block.

    Each replacement is widened to the lines it actually touches. Those lines
    are taken from the normalized base (they had to change anyway); every other
    line is copied verbatim from the original. That is what keeps the curly
    quotes on untouched lines from being flattened by a fuzzy match elsewhere.

    Widening to *actual replacement ranges*, rather than aligning identical
    normalized lines, is deliberate: duplicate lines would otherwise be matched
    to the wrong occurrence.
    """
    original_lines = _split_lines_keeping_endings(original_content)
    base_spans = _line_spans(base_content)
    if len(original_lines) != len(base_spans):
        raise ValueError("Cannot preserve unchanged lines because the base "
                         "content has a different line count.")

    groups: list[dict] = []
    for replacement in sorted(replacements, key=lambda r: r.match_index):
        start_line, end_line = _replacement_line_range(base_spans, replacement)
        if groups and start_line < groups[-1]["end"]:
            groups[-1]["end"] = max(groups[-1]["end"], end_line)
            groups[-1]["replacements"].append(replacement)
            continue
        groups.append({"start": start_line, "end": end_line,
                       "replacements": [replacement]})

    result, line_index = "", 0
    for group in groups:
        result += "".join(original_lines[line_index:group["start"]])
        start_offset = base_spans[group["start"]][0]
        end_offset = base_spans[group["end"] - 1][1]
        result += _apply(base_content[start_offset:end_offset],
                         group["replacements"], start_offset)
        line_index = group["end"]
    return result + "".join(original_lines[line_index:])


def _count_occurrences(content: str, old_text: str) -> int:
    return len(normalize_for_fuzzy_match(content).split(
        normalize_for_fuzzy_match(old_text))) - 1


def _label(index: int, total: int) -> str:
    """Name the offending edit only when there is more than one -- `edits[0]`
    is noise when the caller passed a single edit."""
    return "the text" if total == 1 else f"edits[{index}]"


def apply_edits(normalized_content: str, edits: list[Edit],
                path: str) -> AppliedEditsResult:
    """Apply exact-text replacements to LF-normalized content.

    Every edit is matched against the *same* original content, then applied in
    reverse order so offsets stay stable. If any edit needed fuzzy matching,
    the whole operation runs in normalized space and is overlaid back onto the
    original so untouched lines keep their bytes.
    """
    normalized = [Edit(normalize_to_lf(e.old_text), normalize_to_lf(e.new_text))
                  for e in edits]
    total = len(normalized)

    for index, edit in enumerate(normalized):
        if not edit.old_text:
            raise ValueError(f"{_label(index, total)} must not be empty in {path}. "
                             "oldText cannot be empty.")

    used_fuzzy = any(fuzzy_find_text(normalized_content, e.old_text).used_fuzzy_match
                     for e in normalized)
    base = normalize_for_fuzzy_match(normalized_content) if used_fuzzy else normalized_content

    matched: list[_Matched] = []
    for index, edit in enumerate(normalized):
        match = fuzzy_find_text(base, edit.old_text)
        if not match.found:
            raise ValueError(
                f"Could not find {_label(index, total)} in {path}. The oldText must "
                "match exactly including all whitespace and newlines.")

        occurrences = _count_occurrences(base, edit.old_text)
        if occurrences > 1:
            raise ValueError(
                f"Found {occurrences} occurrences of {_label(index, total)} in {path}. "
                "Each oldText must be unique. Provide more context to make it unique.")

        matched.append(_Matched(index, match.index, match.match_length, edit.new_text))

    matched.sort(key=lambda m: m.match_index)
    for previous, current in zip(matched, matched[1:]):
        if previous.match_index + previous.match_length > current.match_index:
            raise ValueError(
                f"edits[{previous.edit_index}] and edits[{current.edit_index}] overlap "
                f"in {path}. Merge them into one edit or target disjoint regions.")

    new_content = (apply_replacements_preserving_unchanged_lines(
                       normalized_content, base, matched)
                   if used_fuzzy else _apply(base, matched))

    if new_content == normalized_content:
        raise ValueError(
            f"No changes made to {path}. The replacement produced identical content. "
            "This might indicate an issue with special characters or the text not "
            "existing as expected.")

    return AppliedEditsResult(base_content=normalized_content, new_content=new_content)


# --------------------------------------------------------------------------
# diff rendering
# --------------------------------------------------------------------------


def generate_unified_patch(path: str, old_content: str, new_content: str,
                           context_lines: int = 4) -> str:
    return "".join(difflib.unified_diff(
        old_content.splitlines(keepends=True), new_content.splitlines(keepends=True),
        fromfile=path, tofile=path, n=context_lines))


def generate_diff_string(old_content: str, new_content: str,
                         context_lines: int = 4) -> tuple[str, int | None]:
    """Render a diff for the model, plus the first changed line in the new file.

    Context is bounded because this string is fed straight back into the
    context window: a three-line change in a thousand-line file must cost
    three lines of context, not a thousand.
    """
    old_lines = old_content.splitlines()
    new_lines = new_content.splitlines()

    output: list[str] = []
    first_changed: int | None = None
    width = len(str(max(len(old_lines), len(new_lines))))

    for group in difflib.SequenceMatcher(None, old_lines, new_lines).get_grouped_opcodes(
            context_lines):
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                for offset, line in enumerate(old_lines[i1:i2]):
                    output.append(f"{str(j1 + offset + 1).rjust(width)}   {line}")
                continue
            if first_changed is None:
                # For a pure deletion j1 == j2; the change still surfaces at
                # the line that now occupies that position.
                first_changed = j1 + 1
            for line in old_lines[i1:i2]:
                output.append(f"{' ' * width} - {line}")
            for offset, line in enumerate(new_lines[j1:j2]):
                output.append(f"{str(j1 + offset + 1).rjust(width)} + {line}")

    return "\n".join(output), first_changed
