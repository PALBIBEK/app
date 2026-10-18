"""Edit matching and diff generation (port of `harness/tools/edit-diff.ts`).

githi's edit tool is `str.replace`. pi's is 500 lines, and the difference is
all failure modes a naive replace hits in practice:

* **Line endings.** Python's text mode translates `\\r\\n` to `\\n` on read and
  writes back `\\n`. Editing one line of a CRLF file therefore rewrites *every*
  line ending in it -- a one-line edit shows up as a whole-file diff.
* **BOM.** Same story: read it, drop it, write the file back subtly changed.
* **Fuzzy matching.** The model quotes text back from what it read. If the
  file has a curly apostrophe, an en-dash, or a non-breaking space, an exact
  match fails and the model has no way to tell *why*.
* **Batched edits.** Several replacements in one call must all be located
  against the original content and applied back-to-front, or the first edit
  invalidates the offsets of the rest.
* **Overlap detection.** Two edits touching the same span produce silent
  corruption; the only safe answer is to refuse.

The unifying rule: normalize for *matching*, but preserve the original bytes
everywhere the edit did not actually touch.
"""

from __future__ import annotations

import pytest

from githi.tools.edit_diff import (
    Edit,
    apply_edits,
    detect_line_ending,
    fuzzy_find_text,
    generate_diff_string,
    normalize_for_fuzzy_match,
    normalize_to_lf,
    restore_line_endings,
    strip_bom,
)


class TestLineEndings:
    def test_detects_lf(self):
        assert detect_line_ending("a\nb\n") == "\n"

    def test_detects_crlf(self):
        assert detect_line_ending("a\r\nb\r\n") == "\r\n"

    def test_no_newline_defaults_to_lf(self):
        assert detect_line_ending("single line") == "\n"

    def test_mixed_endings_follow_whichever_comes_first(self):
        """A file that is mostly LF with a stray CRLF later is an LF file."""
        assert detect_line_ending("a\nb\r\n") == "\n"
        assert detect_line_ending("a\r\nb\n") == "\r\n"

    def test_normalize_to_lf(self):
        assert normalize_to_lf("a\r\nb\rc\n") == "a\nb\nc\n"

    def test_restore_round_trips(self):
        original = "a\r\nb\r\n"
        assert restore_line_endings(normalize_to_lf(original), "\r\n") == original

    def test_restore_lf_is_a_noop(self):
        assert restore_line_endings("a\nb\n", "\n") == "a\nb\n"


class TestBom:
    def test_strips_and_reports_bom(self):
        bom, text = strip_bom("﻿hello")
        assert bom == "﻿" and text == "hello"

    def test_absent_bom_yields_empty_string(self):
        bom, text = strip_bom("hello")
        assert bom == "" and text == "hello"


class TestFuzzyNormalization:
    def test_strips_trailing_whitespace_per_line(self):
        assert normalize_for_fuzzy_match("a   \nb\t\n") == "a\nb\n"

    def test_smart_single_quotes_become_ascii(self):
        assert normalize_for_fuzzy_match("don’t") == "don't"

    def test_smart_double_quotes_become_ascii(self):
        assert normalize_for_fuzzy_match("“hi”") == '"hi"'

    def test_dashes_become_ascii_hyphen(self):
        assert normalize_for_fuzzy_match("a—b–c−d") == "a-b-c-d"

    def test_unicode_spaces_become_plain_spaces(self):
        assert normalize_for_fuzzy_match("a b c") == "a b c"


class TestFuzzyFind:
    def test_exact_match_wins(self):
        match = fuzzy_find_text("hello world", "world")
        assert match.found and match.index == 6 and not match.used_fuzzy_match

    def test_falls_back_to_fuzzy(self):
        """The model echoes back an ASCII apostrophe for a curly one it read."""
        match = fuzzy_find_text("it’s here", "it's here")
        assert match.found and match.used_fuzzy_match

    def test_genuinely_absent_text_is_not_found(self):
        assert not fuzzy_find_text("hello", "goodbye").found

    def test_fuzzy_offsets_are_in_normalized_space(self):
        """The caller must replace against `content_for_replacement`, not the
        original -- normalization can shift every offset after it."""
        match = fuzzy_find_text("x—y target", "target")
        assert match.content_for_replacement[match.index:
                                             match.index + match.match_length] == "target"


class TestApplyEdits:
    def test_single_replacement(self):
        result = apply_edits("hello world", [Edit("world", "there")], "f.txt")
        assert result.new_content == "hello there"

    def test_multiple_edits_in_one_call(self):
        result = apply_edits("a b c", [Edit("a", "1"), Edit("c", "3")], "f.txt")
        assert result.new_content == "1 b 3"

    def test_later_edits_are_not_shifted_by_earlier_ones(self):
        """All edits match against the original; applying front-to-back would
        invalidate every offset after the first."""
        result = apply_edits("xxx yyy zzz",
                             [Edit("xxx", "LONGER-REPLACEMENT"), Edit("zzz", "z")],
                             "f.txt")
        assert result.new_content == "LONGER-REPLACEMENT yyy z"

    def test_empty_old_text_is_refused(self):
        with pytest.raises(ValueError, match="empty"):
            apply_edits("content", [Edit("", "x")], "f.txt")

    def test_missing_text_names_the_file(self):
        with pytest.raises(ValueError, match="f.txt"):
            apply_edits("content", [Edit("absent", "x")], "f.txt")

    def test_ambiguous_text_is_refused(self):
        """Replacing the first of several identical matches is wrong about as
        often as it is right, and silently."""
        with pytest.raises(ValueError, match="2 occurrences"):
            apply_edits("dup dup", [Edit("dup", "x")], "f.txt")

    def test_error_indexes_the_edit_when_batched(self):
        with pytest.raises(ValueError, match=r"edits\[1\]"):
            apply_edits("a b", [Edit("a", "1"), Edit("absent", "2")], "f.txt")

    def test_overlapping_edits_are_refused(self):
        with pytest.raises(ValueError, match="overlap"):
            apply_edits("abcdef", [Edit("abcd", "x"), Edit("cdef", "y")], "f.txt")

    def test_a_no_op_edit_is_an_error(self):
        """Identical output means the edit did not do what the model thought."""
        with pytest.raises(ValueError, match="No changes"):
            apply_edits("same", [Edit("same", "same")], "f.txt")

    def test_fuzzy_edit_preserves_untouched_lines_byte_for_byte(self):
        """The whole point: match loosely, but only rewrite the lines the edit
        actually touched. Other lines keep their original characters."""
        content = "keep — this\nit’s here\nkeep “that”\n"
        result = apply_edits(content, [Edit("it's here", "replaced")], "f.txt")
        assert "replaced" in result.new_content
        assert "keep — this" in result.new_content
        assert "keep “that”" in result.new_content


class TestDiffString:
    def test_reports_the_first_changed_line(self):
        diff, first = generate_diff_string("a\nb\nc\n", "a\nCHANGED\nc\n")
        assert first == 2

    def test_shows_removed_and_added_lines(self):
        diff, _ = generate_diff_string("a\nb\n", "a\nc\n")
        assert "-" in diff and "+" in diff and "b" in diff and "c" in diff

    def test_identical_content_has_no_changed_line(self):
        diff, first = generate_diff_string("same\n", "same\n")
        assert first is None

    def test_context_lines_are_limited(self):
        """A three-line change in a thousand-line file must not print the
        thousand lines -- the result goes into the model's context."""
        old = "\n".join(str(i) for i in range(200))
        new = old.replace("100", "CHANGED")
        diff, _ = generate_diff_string(old, new, context_lines=2)
        assert len(diff.splitlines()) < 20
