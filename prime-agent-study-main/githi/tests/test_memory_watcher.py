"""External-edit detection and archiving (ports of `watcher.rs`, `archive.rs`).

Markdown is the source of truth, which means a human can edit `MEMORY.md` in
a text editor at any time — and the index must notice. Grok watches the
directory and reconciles before each query.

The deletion half is the part that is easy to omit and expensive to get
wrong: Grok's own docstring says *"Without the deletion handling, chunks from
removed files would remain searchable indefinitely."* A user who deletes a
memory expects it gone, not still surfacing in results.

Polling by mtime rather than an OS notification API: no dependency, works
identically on every platform and over network filesystems, and the cost is
one `stat` per file on a directory that holds a handful of markdown files.
"""

from __future__ import annotations

import os
import tarfile
import time

import pytest

from githi.memory.archive import build_archive
from githi.memory.index import MemoryIndex
from githi.memory.storage import MemoryScope, MemoryStorage
from githi.memory.watcher import MemoryWatcher


@pytest.fixture
def setup(tmp_path):
    storage = MemoryStorage(home=tmp_path / "home", workspace=tmp_path / "proj")
    index = MemoryIndex(tmp_path / "i.db")
    yield storage, index
    index.close()


def _touch_future(path) -> None:
    """Bump mtime past 'now' -- filesystem resolution is too coarse for a
    real sleep to be reliable here."""
    when = time.time() + 5
    os.utime(path, (when, when))


class TestDirtyDetection:
    def test_new_file_is_detected(self, setup):
        storage, index = setup
        watcher = MemoryWatcher(storage)
        watcher.scan()                                  # establish a baseline
        storage.write(MemoryScope.WORKSPACE, "# New\n\nbody\n")
        assert watcher.scan().created

    def test_modified_file_is_detected(self, setup):
        storage, index = setup
        storage.write(MemoryScope.WORKSPACE, "# A\n\noriginal\n")
        watcher = MemoryWatcher(storage)
        watcher.scan()

        storage.write(MemoryScope.WORKSPACE, "# A\n\nedited\n")
        _touch_future(storage.workspace_memory_path())
        assert watcher.scan().modified

    def test_deleted_file_is_detected(self, setup):
        storage, index = setup
        path = storage.append_session("s", "# S\n\nbody\n", run_id="aaaaaaaa")
        watcher = MemoryWatcher(storage)
        watcher.scan()

        storage.delete_session(path)
        assert watcher.scan().deleted

    def test_unchanged_files_report_nothing(self, setup):
        storage, index = setup
        storage.write(MemoryScope.WORKSPACE, "# A\n\nbody\n")
        watcher = MemoryWatcher(storage)
        watcher.scan()

        result = watcher.scan()
        assert not result.created and not result.modified and not result.deleted
        assert not result.any_changes

    def test_first_scan_reports_existing_files_as_created(self, setup):
        storage, index = setup
        storage.write(MemoryScope.WORKSPACE, "# A\n\nbody\n")
        assert MemoryWatcher(storage).scan().created

    def test_missing_directories_are_not_an_error(self, setup):
        storage, index = setup
        assert not MemoryWatcher(storage).scan().any_changes


class TestSync:
    def test_new_content_becomes_searchable(self, setup):
        storage, index = setup
        watcher = MemoryWatcher(storage)
        storage.write(MemoryScope.WORKSPACE, "# Limits\n\nrate limit is 60 per hour\n")
        watcher.sync(index)
        assert index.search_fts("rate limit")

    def test_external_edit_is_reindexed(self, setup):
        """The reason the watcher exists: markdown is editable outside the
        agent, and a stale index would keep answering with the old text."""
        storage, index = setup
        watcher = MemoryWatcher(storage)
        storage.write(MemoryScope.WORKSPACE, "# A\n\nthe limit is sixty\n")
        watcher.sync(index)

        storage.write(MemoryScope.WORKSPACE, "# A\n\nthe limit is ninety\n")
        _touch_future(storage.workspace_memory_path())
        watcher.sync(index)

        assert index.search_fts("ninety")
        assert not index.search_fts("sixty")

    def test_deleted_file_chunks_are_purged(self, setup):
        """Grok: 'without the deletion handling, chunks from removed files
        would remain searchable indefinitely.'"""
        storage, index = setup
        watcher = MemoryWatcher(storage)
        path = storage.append_session("s", "# S\n\nephemeral detail\n", run_id="aaaaaaaa")
        watcher.sync(index)
        assert index.search_fts("ephemeral")

        storage.delete_session(path)
        watcher.sync(index)
        assert index.search_fts("ephemeral") == [], "deleted memory is still searchable"

    def test_sync_tags_the_correct_source(self, setup):
        storage, index = setup
        watcher = MemoryWatcher(storage)
        storage.write(MemoryScope.GLOBAL, "# G\n\nglobal fact\n")
        storage.write(MemoryScope.WORKSPACE, "# W\n\nworkspace fact\n")
        storage.append_session("s", "# S\n\nsession fact\n", run_id="aaaaaaaa")
        watcher.sync(index)

        sources = {c.source for c in index.all_chunks()}
        assert sources == {"global", "workspace", "session"}

    def test_sync_is_idempotent(self, setup):
        """Re-syncing an unchanged corpus must do no work -- otherwise every
        query re-indexes (and later re-embeds) everything."""
        storage, index = setup
        watcher = MemoryWatcher(storage)
        storage.write(MemoryScope.WORKSPACE, "# A\n\nbody text here\n")
        watcher.sync(index)
        before = index.chunk_rowids()

        watcher.sync(index)
        assert index.chunk_rowids() == before

    def test_sync_returns_what_it_did(self, setup):
        storage, index = setup
        watcher = MemoryWatcher(storage)
        storage.write(MemoryScope.WORKSPACE, "# A\n\nbody\n")
        assert watcher.sync(index).any_changes
        assert not watcher.sync(index).any_changes


class TestArchive:
    def test_creates_a_tarball(self, setup, tmp_path):
        storage, index = setup
        storage.write(MemoryScope.WORKSPACE, "# W\n\nfact\n")
        storage.append_session("s", "# S\n\nlog\n", run_id="aaaaaaaa")

        archive = build_archive(storage, tmp_path / "memory.tar.gz")
        assert archive.exists() and tarfile.is_tarfile(archive)

    def test_contains_memory_and_sessions(self, setup, tmp_path):
        storage, index = setup
        storage.write(MemoryScope.GLOBAL, "# G\n\nglobal fact\n")
        storage.write(MemoryScope.WORKSPACE, "# W\n\nworkspace fact\n")
        storage.append_session("s", "# S\n\nsession log\n", run_id="aaaaaaaa")

        archive = build_archive(storage, tmp_path / "memory.tar.gz")
        with tarfile.open(archive, "r:gz") as tar:
            names = tar.getnames()
        assert any("MEMORY.md" in n for n in names)
        assert any("sessions" in n for n in names)

    def test_excludes_the_index(self, setup, tmp_path):
        """The database is disposable and rebuildable; shipping it would
        bloat the archive and risk restoring a stale index over fresh
        markdown."""
        storage, index = setup
        storage.write(MemoryScope.WORKSPACE, "# W\n\nfact\n")
        (storage.workspace_dir() / "index.sqlite3").write_bytes(b"not markdown")

        archive = build_archive(storage, tmp_path / "memory.tar.gz")
        with tarfile.open(archive, "r:gz") as tar:
            assert not any(n.endswith(".sqlite3") for n in tar.getnames())

    def test_empty_memory_still_produces_a_valid_archive(self, setup, tmp_path):
        storage, index = setup
        archive = build_archive(storage, tmp_path / "memory.tar.gz")
        assert tarfile.is_tarfile(archive)

    def test_archive_round_trips(self, setup, tmp_path):
        storage, index = setup
        storage.write(MemoryScope.WORKSPACE, "# W\n\na durable fact\n")
        archive = build_archive(storage, tmp_path / "memory.tar.gz")

        target = tmp_path / "restored"
        with tarfile.open(archive, "r:gz") as tar:
            tar.extractall(target)
        assert any("a durable fact" in p.read_text()
                   for p in target.rglob("*.md"))
