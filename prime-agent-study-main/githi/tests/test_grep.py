"""grep -- regex search across sandboxed files.

Implemented in Python rather than shelling out to ripgrep/grep so behaviour
does not depend on which binaries happen to exist on the host. A tool whose
output format changes between machines is one the model cannot learn to use.
"""

from __future__ import annotations

import pytest

from githi.sandbox.policy import SandboxPolicy
from githi.tools.base import ToolContext
from githi.tools.grep import grep_tool


@pytest.fixture
def ctx(tmp_path):
    (tmp_path / "a.py").write_text("import os\ndef handler():\n    return os.getcwd()\n")
    (tmp_path / "b.py").write_text("import sys\ndef main():\n    pass\n")
    (tmp_path / "notes.md").write_text("handler docs\n")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "c.py").write_text("def handler():\n    pass\n")
    return ToolContext(policy=SandboxPolicy(root=tmp_path))


def _grep(ctx, **args):
    return grep_tool().run(args, ctx)


class TestModes:
    def test_files_with_matches_is_the_default(self, ctx):
        """Cheapest mode first: which files match at all, without paying for
        the content of every hit."""
        out = _grep(ctx, pattern="handler").text
        assert "a.py" in out and "notes.md" in out
        assert "def handler" not in out

    def test_content_mode_shows_matching_lines(self, ctx):
        out = _grep(ctx, pattern="def handler", output_mode="content").text
        assert "def handler():" in out

    def test_content_mode_includes_line_numbers(self, ctx):
        assert ":2:" in _grep(ctx, pattern="def handler", output_mode="content").text

    def test_count_mode(self, ctx):
        out = _grep(ctx, pattern="handler", output_mode="count").text
        assert "a.py" in out and "1" in out

    def test_invalid_mode_is_a_correctable_error(self, ctx):
        assert _grep(ctx, pattern="x", output_mode="sideways").is_error


class TestFiltering:
    def test_glob_narrows_the_search(self, ctx):
        out = _grep(ctx, pattern="handler", glob="*.py").text
        assert "a.py" in out and "notes.md" not in out

    def test_searches_subdirectories(self, ctx):
        assert "c.py" in _grep(ctx, pattern="handler", glob="**/*.py").text

    def test_case_insensitive(self, ctx):
        assert "a.py" in _grep(ctx, pattern="HANDLER", case_insensitive=True).text

    def test_case_sensitive_by_default(self, ctx):
        assert _grep(ctx, pattern="HANDLER").text.strip().lower().startswith("no match")

    def test_path_narrows_to_one_file(self, ctx):
        out = _grep(ctx, pattern="import", path="b.py").text
        assert "b.py" in out and "a.py" not in out


class TestRobustness:
    def test_invalid_regex_is_a_correctable_error(self, ctx):
        result = _grep(ctx, pattern="([unclosed")
        assert result.is_error and "regex" in result.text.lower()

    def test_no_matches_is_not_an_error(self, ctx):
        """Finding nothing is a valid answer, not a failure."""
        result = _grep(ctx, pattern="zzzznotfound")
        assert not result.is_error and "no match" in result.text.lower()

    def test_escape_attempt_is_refused(self, ctx):
        assert _grep(ctx, pattern="root", path="/etc/passwd").is_error

    def test_binary_files_are_skipped_not_fatal(self, ctx):
        (ctx.policy.root / "blob.bin").write_bytes(b"\x00\x01\x02handler\xff")
        assert not _grep(ctx, pattern="handler").is_error

    def test_results_are_capped(self, ctx):
        """An unbounded grep can dump more into context than the run can
        afford; the cap must be visible so the model knows to narrow."""
        big = ctx.policy.root / "many.txt"
        big.write_text("match\n" * 5000)
        out = _grep(ctx, pattern="match", output_mode="content", head_limit=10).text
        assert out.count("match") <= 20
        assert "limit" in out.lower() or "showing" in out.lower()
