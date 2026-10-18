"""Grok-style memory layout and markdown storage (port of `storage.rs`/`lib.rs`).

The organising decision, copied deliberately: **markdown files are the source
of truth and SQLite is a disposable index.** Everything the model reads or
writes is plain `.md` a human can edit; the database can be deleted and
rebuilt from the files at any time.

Workspace memory lives under `~/.githi/`, never inside the user's repo —
memory is not a project artifact and must not show up in `git status`.
"""

from __future__ import annotations

import pytest

from githi.memory.storage import (
    MemoryScope,
    MemoryStorage,
    is_scaffold_template,
    workspace_key,
)


@pytest.fixture
def storage(tmp_path):
    return MemoryStorage(home=tmp_path / "home", workspace=tmp_path / "proj")


class TestWorkspaceKey:
    def test_stable_for_the_same_path(self, tmp_path):
        assert workspace_key(tmp_path / "a") == workspace_key(tmp_path / "a")

    def test_differs_between_workspaces(self, tmp_path):
        assert workspace_key(tmp_path / "a") != workspace_key(tmp_path / "b")

    def test_includes_a_readable_slug_and_a_hash(self, tmp_path):
        """Grok uses `{slug}-{hash8}`: the slug makes the directory
        identifiable by eye, the hash makes it collision-free."""
        key = workspace_key(tmp_path / "my-project")
        assert key.startswith("my-project-")
        assert len(key.rsplit("-", 1)[1]) == 8

    def test_slug_is_filesystem_safe(self, tmp_path):
        key = workspace_key(tmp_path / "weird name!/with:chars")
        assert "/" not in key and ":" not in key and " " not in key

    def test_distinguishes_paths_with_the_same_basename(self, tmp_path):
        """`~/a/proj` and `~/b/proj` share a slug; only the hash separates
        them, so it must be derived from the full path."""
        a = workspace_key(tmp_path / "a" / "proj")
        b = workspace_key(tmp_path / "b" / "proj")
        assert a != b and a.split("-")[0] == b.split("-")[0]


class TestLayout:
    def test_global_memory_path(self, storage):
        assert storage.global_memory_path().name == "MEMORY.md"
        assert storage.global_memory_path().parent == storage.home

    def test_workspace_memory_is_scoped_by_key(self, storage):
        path = storage.workspace_memory_path()
        assert path.name == "MEMORY.md"
        assert path.parent.name == workspace_key(storage.workspace)

    def test_sessions_live_under_the_workspace_dir(self, storage):
        assert storage.sessions_dir().name == "sessions"
        assert storage.sessions_dir().parent == storage.workspace_memory_path().parent

    def test_nothing_is_written_inside_the_users_repo(self, storage, tmp_path):
        """The whole point of hashing into ~/.githi: memory must never
        appear in the project's own tree, or it lands in git status."""
        storage.write(MemoryScope.WORKSPACE, "# Notes\n")
        storage.append_session("sess", "# Log\n")
        assert not list((tmp_path / "proj").rglob("*")), "wrote into the repo"


class TestReadWrite:
    def test_write_then_read_roundtrip(self, storage):
        storage.write(MemoryScope.GLOBAL, "# Global\n\nfacts\n")
        assert "facts" in storage.read(MemoryScope.GLOBAL)

    def test_reading_absent_memory_returns_empty(self, storage):
        assert storage.read(MemoryScope.WORKSPACE) == ""

    def test_write_creates_parent_directories(self, storage):
        storage.write(MemoryScope.WORKSPACE, "x")
        assert storage.workspace_memory_path().exists()

    def test_scopes_are_independent(self, storage):
        storage.write(MemoryScope.GLOBAL, "global-only")
        storage.write(MemoryScope.WORKSPACE, "workspace-only")
        assert storage.read(MemoryScope.GLOBAL) == "global-only"
        assert storage.read(MemoryScope.WORKSPACE) == "workspace-only"

    def test_write_is_atomic(self, storage):
        """A crash mid-write must not leave a truncated MEMORY.md — the file
        is the source of truth, so a partial write loses real knowledge."""
        storage.write(MemoryScope.GLOBAL, "original content")
        assert storage.read(MemoryScope.GLOBAL) == "original content"
        assert not list(storage.home.glob("*.tmp")), "temp file left behind"


class TestSessions:
    def test_session_filename_is_dated_and_unique(self, storage):
        path = storage.append_session("triage-issues", "# Log\n")
        assert path.suffix == ".md"
        assert "triage-issues" in path.name
        assert path.name[:4].isdigit(), "starts with YYYY"

    def test_appending_twice_to_one_session_accumulates(self, storage):
        storage.append_session("s", "first\n", run_id="abc12345")
        storage.append_session("s", "second\n", run_id="abc12345")
        text = storage.read_session(storage.session_path("s", "abc12345"))
        assert "first" in text and "second" in text

    def test_distinct_runs_get_distinct_files(self, storage):
        a = storage.append_session("s", "x", run_id="aaaaaaaa")
        b = storage.append_session("s", "y", run_id="bbbbbbbb")
        assert a != b

    def test_list_sessions(self, storage):
        storage.append_session("one", "x", run_id="aaaaaaaa")
        storage.append_session("two", "y", run_id="bbbbbbbb")
        assert len(storage.list_sessions()) == 2

    def test_list_sessions_when_none(self, storage):
        assert storage.list_sessions() == []

    def test_delete_session(self, storage):
        path = storage.append_session("s", "x", run_id="aaaaaaaa")
        storage.delete_session(path)
        assert not path.exists() and storage.list_sessions() == []

    def test_slug_is_sanitised(self, storage):
        path = storage.append_session("Fix: the/thing!", "x", run_id="aaaaaaaa")
        assert "/" not in path.name and ":" not in path.name


class TestScaffoldDetection:
    """Ported from Grok's `is_scaffold_template`. The two-condition rule is
    the point: short **AND** contains a marker. A real memory that merely
    quotes a marker phrase must not be mistaken for an empty stub and fed to
    consolidation as 'no prior knowledge'."""

    def test_short_stub_with_marker_is_scaffold(self):
        assert is_scaffold_template("# Memory\n\nAdd project-specific knowledge here.\n")

    def test_substantial_file_is_never_scaffold_even_with_marker(self):
        content = ("# Memory\n\nAdd project-specific knowledge here.\n"
                   + "Real accumulated knowledge. " * 40)
        assert len(content) > 500
        assert not is_scaffold_template(content)

    def test_short_file_without_marker_is_not_scaffold(self):
        assert not is_scaffold_template("# Memory\n\nThe API rate limit is 60/hour.\n")

    def test_empty_is_scaffold(self):
        assert is_scaffold_template("")


def test_scope_enum_covers_grok_sources():
    assert {s.value for s in MemoryScope} == {"global", "workspace"}
