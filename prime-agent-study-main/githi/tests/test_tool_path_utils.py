"""Path normalisation for tool arguments (port of `harness/tools/path-utils.ts`).

Every one of these rules exists because a path that *looks* correct on screen
does not match the bytes on disk. The model copies a path from output a human
pasted, the filesystem disagrees, and the tool reports "no such file" for a
file that is plainly there.

Two classes of fix:

* **Normalisation** — Unicode spaces and pi's `@file` reference syntax, applied
  to every path.
* **Variant probing** — read-only: try several plausible encodings of the same
  name and use whichever exists. macOS is the main offender (screenshot
  filenames use a narrow no-break space; HFS+/APFS store NFD while
  copy-paste yields NFC; "smart quotes" replace apostrophes).
"""

from __future__ import annotations

import unicodedata

import pytest

from githi.tools.path_utils import normalize_tool_path, resolve_read_path


class TestNormalisation:
    @pytest.mark.parametrize("space", [
        " ",   # no-break space
        " ",   # en quad
        " ",   # thin space
        " ",   # narrow no-break space
        " ",   # medium mathematical space
        "　",   # ideographic space
    ])
    def test_unicode_spaces_become_plain_spaces(self, space):
        assert normalize_tool_path(f"my{space}file.txt") == "my file.txt"

    def test_leading_at_is_stripped(self):
        """pi uses `@path` for file references in prompts; the `@` reaches
        tools verbatim and is not part of any filename."""
        assert normalize_tool_path("@src/main.py") == "src/main.py"

    def test_at_elsewhere_is_preserved(self):
        """Only a *leading* `@` is syntax -- `mail@host.txt` is a filename."""
        assert normalize_tool_path("mail@host.txt") == "mail@host.txt"

    def test_ordinary_paths_are_untouched(self):
        assert normalize_tool_path("src/githi/main.py") == "src/githi/main.py"

    def test_empty_path(self):
        assert normalize_tool_path("") == ""


class TestReadVariants:
    def test_exact_match_wins(self, tmp_path):
        (tmp_path / "plain.txt").write_text("x")
        assert resolve_read_path(tmp_path / "plain.txt").name == "plain.txt"

    def test_narrow_no_break_space_before_am_pm(self, tmp_path):
        """The macOS screenshot case: the file on disk uses U+202F before
        AM/PM, but pasting the name yields a normal space."""
        real = tmp_path / "Screenshot at 10.30.45 AM.png"
        real.write_text("x")

        typed = tmp_path / "Screenshot at 10.30.45 AM.png"
        assert resolve_read_path(typed) == real

    def test_nfd_variant_is_found(self, tmp_path):
        """macOS filesystems store decomposed (NFD); a pasted name is usually
        composed (NFC). The bytes differ, the display is identical."""
        decomposed = unicodedata.normalize("NFD", "café.txt")
        (tmp_path / decomposed).write_text("x")

        composed = tmp_path / unicodedata.normalize("NFC", "café.txt")
        assert resolve_read_path(composed).exists()

    def test_curly_apostrophe_variant_is_found(self, tmp_path):
        """Editors and macOS 'smart quotes' turn ' into U+2019 in filenames."""
        real = tmp_path / "Bob’s notes.txt"
        real.write_text("x")

        typed = tmp_path / "Bob's notes.txt"
        assert resolve_read_path(typed) == real

    def test_missing_file_returns_the_resolved_path_unchanged(self, tmp_path):
        """No variant exists, so the caller gets the path it asked for and
        reports a normal 'not found' -- guessing a different file would be
        worse than failing."""
        target = tmp_path / "absent.txt"
        assert resolve_read_path(target) == target

    def test_variants_do_not_invent_a_different_file(self, tmp_path):
        """Probing must never fall back to a *similar* name -- silently
        reading the wrong file is the worst possible outcome."""
        (tmp_path / "notes-backup.txt").write_text("x")
        assert not resolve_read_path(tmp_path / "notes.txt").exists()

    def test_normalisation_is_applied_before_probing(self, tmp_path):
        (tmp_path / "my file.txt").write_text("x")
        assert resolve_read_path(tmp_path / "my file.txt").exists()
