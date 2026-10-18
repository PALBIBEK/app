"""read / write / edit / ls / find.

Every one of these takes a path from the model, so every one is a potential
sandbox escape. The confinement logic lives in `sandbox.paths`; what these
tests check is that each tool actually *routes through* it -- a tool that
forgets is the whole vulnerability.
"""

from __future__ import annotations

import pytest

from githi.sandbox.policy import SandboxPolicy
from githi.tools.base import ToolContext
from githi.tools.edit import edit_tool
from githi.tools.ls import find_tool, ls_tool
from githi.tools.read import read_tool
from githi.tools.write import write_tool


@pytest.fixture
def ctx(tmp_path):
    (tmp_path / "notes.txt").write_text("alpha\nbeta\ngamma\n")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text("def f():\n    return 1\n")
    return ToolContext(policy=SandboxPolicy(root=tmp_path))


class TestRead:
    def test_reads_a_file(self, ctx):
        assert "beta" in read_tool().run({"path": "notes.txt"}, ctx).text

    def test_missing_file_is_a_correctable_error(self, ctx):
        result = read_tool().run({"path": "nope.txt"}, ctx)
        assert result.is_error and "nope.txt" in result.text

    def test_line_numbers_help_the_edit_tool(self, ctx):
        """`edit` works by exact string match, so the model needs to see the
        file the way it actually is."""
        assert "1" in read_tool().run({"path": "notes.txt"}, ctx).text

    def test_escape_attempt_is_refused(self, ctx):
        assert read_tool().run({"path": "../../../etc/passwd"}, ctx).is_error

    def test_directory_is_a_clear_error_not_a_crash(self, ctx):
        assert read_tool().run({"path": "pkg"}, ctx).is_error

    def test_large_file_is_truncated(self, ctx):
        (ctx.policy.root / "big.txt").write_text("x" * 200_000)
        assert read_tool().run({"path": "big.txt"}, ctx).text.count("x") < 200_000


class TestWrite:
    def test_creates_a_file(self, ctx):
        write_tool().run({"path": "new.txt", "content": "hi"}, ctx)
        assert (ctx.policy.root / "new.txt").read_text() == "hi"

    def test_creates_parent_directories(self, ctx):
        write_tool().run({"path": "a/b/c.txt", "content": "x"}, ctx)
        assert (ctx.policy.root / "a" / "b" / "c.txt").exists()

    def test_overwrites(self, ctx):
        write_tool().run({"path": "notes.txt", "content": "replaced"}, ctx)
        assert (ctx.policy.root / "notes.txt").read_text() == "replaced"

    def test_escape_attempt_is_refused(self, ctx, tmp_path_factory):
        outside = tmp_path_factory.mktemp("out") / "pwned.txt"
        assert write_tool().run({"path": str(outside), "content": "x"}, ctx).is_error
        assert not outside.exists()

    def test_read_only_policy_blocks_writes(self, tmp_path):
        ctx = ToolContext(policy=SandboxPolicy(root=tmp_path, writable=False))
        assert write_tool().run({"path": "x.txt", "content": "y"}, ctx).is_error
        assert not (tmp_path / "x.txt").exists()


class TestEdit:
    def test_replaces_exact_string(self, ctx):
        edit_tool().run({"path": "notes.txt", "old": "beta", "new": "BETA"}, ctx)
        assert "BETA" in (ctx.policy.root / "notes.txt").read_text()

    def test_missing_string_is_an_error_and_changes_nothing(self, ctx):
        before = (ctx.policy.root / "notes.txt").read_text()
        assert edit_tool().run({"path": "notes.txt", "old": "zzz", "new": "x"}, ctx).is_error
        assert (ctx.policy.root / "notes.txt").read_text() == before

    def test_ambiguous_match_is_refused(self, ctx):
        """Replacing the first of several identical matches silently edits the
        wrong line as often as the right one."""
        (ctx.policy.root / "dup.txt").write_text("a\na\n")
        result = edit_tool().run({"path": "dup.txt", "old": "a", "new": "b"}, ctx)
        assert result.is_error and "2" in result.text

    def test_replace_all_allows_ambiguity_explicitly(self, ctx):
        (ctx.policy.root / "dup.txt").write_text("a\na\n")
        edit_tool().run({"path": "dup.txt", "old": "a", "new": "b", "replace_all": True}, ctx)
        assert (ctx.policy.root / "dup.txt").read_text() == "b\nb\n"

    def test_read_only_policy_blocks_edits(self, tmp_path):
        (tmp_path / "f.txt").write_text("a")
        ctx = ToolContext(policy=SandboxPolicy(root=tmp_path, writable=False))
        assert edit_tool().run({"path": "f.txt", "old": "a", "new": "b"}, ctx).is_error


class TestEditPreservesBytes:
    """The edit tool routes through `edit_diff`, so the file keeps everything
    the edit did not deliberately change."""

    def test_crlf_file_keeps_crlf(self, ctx):
        """Python's text mode translates newlines both ways. Without explicit
        handling, changing one word rewrites every line ending in the file --
        a one-line edit that shows up in review as a whole-file diff."""
        target = ctx.policy.root / "win.txt"
        target.write_bytes(b"alpha\r\nbeta\r\ngamma\r\n")
        edit_tool().run({"path": "win.txt", "old": "beta", "new": "BETA"}, ctx)
        assert target.read_bytes() == b"alpha\r\nBETA\r\ngamma\r\n"

    def test_lf_file_stays_lf(self, ctx):
        target = ctx.policy.root / "unix.txt"
        target.write_bytes(b"alpha\nbeta\n")
        edit_tool().run({"path": "unix.txt", "old": "beta", "new": "BETA"}, ctx)
        assert b"\r\n" not in target.read_bytes()

    def test_bom_survives(self, ctx):
        target = ctx.policy.root / "bom.txt"
        target.write_bytes("﻿alpha\nbeta\n".encode("utf-8"))
        edit_tool().run({"path": "bom.txt", "old": "beta", "new": "BETA"}, ctx)
        assert target.read_bytes().startswith(b"\xef\xbb\xbf")

    def test_fuzzy_match_finds_a_curly_apostrophe(self, ctx):
        """The model reads the file, echoes the text back with an ASCII
        apostrophe, and an exact match fails for a reason it cannot see."""
        target = ctx.policy.root / "prose.txt"
        target.write_text("it’s here\nkeep “this”\n", encoding="utf-8")
        result = edit_tool().run(
            {"path": "prose.txt", "old": "it's here", "new": "replaced"}, ctx)
        assert not result.is_error
        assert "replaced" in target.read_text(encoding="utf-8")

    def test_fuzzy_match_leaves_other_lines_alone(self, ctx):
        """Matching loosely must not license rewriting the whole file in
        normalized form -- untouched lines keep their original characters."""
        target = ctx.policy.root / "prose.txt"
        target.write_text("it’s here\nkeep “this”\n", encoding="utf-8")
        edit_tool().run({"path": "prose.txt", "old": "it's here", "new": "x"}, ctx)
        assert "keep “this”" in target.read_text(encoding="utf-8")


class TestEditBatch:
    def test_multiple_edits_in_one_call(self, ctx):
        result = edit_tool().run({"path": "notes.txt", "edits": [
            {"old": "alpha", "new": "A"}, {"old": "gamma", "new": "G"}]}, ctx)
        assert not result.is_error
        assert (ctx.policy.root / "notes.txt").read_text() == "A\nbeta\nG\n"

    def test_a_failing_edit_aborts_the_whole_batch(self, ctx):
        """All-or-nothing: half-applied edits leave the file in a state
        neither the model nor the user asked for."""
        before = (ctx.policy.root / "notes.txt").read_text()
        result = edit_tool().run({"path": "notes.txt", "edits": [
            {"old": "alpha", "new": "A"}, {"old": "absent", "new": "x"}]}, ctx)
        assert result.is_error
        assert (ctx.policy.root / "notes.txt").read_text() == before

    def test_overlapping_edits_are_refused(self, ctx):
        result = edit_tool().run({"path": "notes.txt", "edits": [
            {"old": "alpha\nbeta", "new": "x"},
            {"old": "beta\ngamma", "new": "y"}]}, ctx)
        assert result.is_error and "overlap" in result.text.lower()

    def test_result_shows_a_diff(self, ctx):
        """The model needs to see what it actually changed; 'ok' invites it to
        assume the edit did what it intended."""
        result = edit_tool().run(
            {"path": "notes.txt", "old": "beta", "new": "BETA"}, ctx)
        assert "BETA" in result.text and "beta" in result.text


class TestLsAndFind:
    def test_ls_lists_entries(self, ctx):
        out = ls_tool().run({"path": "."}, ctx).text
        assert "notes.txt" in out and "pkg" in out

    def test_ls_marks_directories(self, ctx):
        assert "pkg/" in ls_tool().run({"path": "."}, ctx).text

    def test_ls_escape_refused(self, ctx):
        assert ls_tool().run({"path": "/etc"}, ctx).is_error

    def test_find_by_glob(self, ctx):
        assert "pkg/mod.py" in find_tool().run({"pattern": "**/*.py"}, ctx).text

    def test_find_reports_no_matches_clearly(self, ctx):
        result = find_tool().run({"pattern": "**/*.rs"}, ctx)
        assert not result.is_error and "no" in result.text.lower()

    def test_find_results_are_relative_to_root(self, ctx):
        """Absolute paths would leak the host layout and are useless as input
        to the other tools, which all resolve relative to root."""
        assert str(ctx.policy.root) not in find_tool().run({"pattern": "**/*.py"}, ctx).text
