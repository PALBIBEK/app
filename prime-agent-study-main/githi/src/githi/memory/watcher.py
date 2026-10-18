"""Detecting edits made outside the agent (port of Grok's `watcher.rs`).

Markdown is the source of truth, so a human can open `MEMORY.md` in an editor
and change it at any time. The index must notice, or search keeps answering
with text the file no longer contains.

**Polling by mtime, not an OS notification API.** Grok uses `notify` with
lock-free dirty tracking because it runs a long-lived daemon. githi is
invoked per run, so a filesystem watcher would have nothing to watch: the
process starts, works, exits. One `stat` per file over a directory holding a
handful of markdown files is cheaper than the dependency, and behaves
identically on every platform and over network filesystems where inotify
does not fire.

The deletion half is the one people leave out. Grok's own note: *"Without the
deletion handling, chunks from removed files would remain searchable
indefinitely."* A user who deletes a memory expects it gone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .index import MemoryIndex
from .storage import MemoryScope, MemoryStorage


@dataclass
class ScanResult:
    created: list[Path] = field(default_factory=list)
    modified: list[Path] = field(default_factory=list)
    deleted: list[Path] = field(default_factory=list)

    @property
    def any_changes(self) -> bool:
        return bool(self.created or self.modified or self.deleted)


class MemoryWatcher:
    """Tracks `(path -> mtime)` between scans."""

    def __init__(self, storage: MemoryStorage) -> None:
        self.storage = storage
        self._seen: dict[Path, float] = {}

    def _current(self) -> dict[Path, float]:
        found: dict[Path, float] = {}
        candidates = [
            self.storage.global_memory_path(),
            self.storage.workspace_memory_path(),
            *self.storage.list_sessions(),
        ]
        for path in candidates:
            try:
                found[path] = path.stat().st_mtime
            except OSError:
                continue                      # absent is normal, not an error
        return found

    def scan(self) -> ScanResult:
        current = self._current()
        result = ScanResult(
            created=[p for p in current if p not in self._seen],
            modified=[p for p, m in current.items()
                      if p in self._seen and m > self._seen[p]],
            deleted=[p for p in self._seen if p not in current],
        )
        self._seen = current
        return result

    def sync(self, index: MemoryIndex) -> ScanResult:
        """Reconcile the index with what is actually on disk.

        Created and modified files are re-indexed; deleted files have their
        chunks purged. Re-indexing an unchanged file is already a no-op
        thanks to content hashing, so this stays cheap enough to call before
        every query.
        """
        result = self.scan()

        for path in [*result.created, *result.modified]:
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            index.index_file(self._key(path), content, source=self._source(path))

        for path in result.deleted:
            index.delete_path(self._key(path))

        return result

    def _key(self, path: Path) -> str:
        """The index key for a file.

        Relative to the memory home so keys stay stable if the home moves,
        and so two workspaces cannot collide on a bare `MEMORY.md`.
        """
        try:
            return str(path.relative_to(self.storage.home))
        except ValueError:
            return str(path)

    def _source(self, path: Path) -> str:
        if path == self.storage.global_memory_path():
            return MemoryScope.GLOBAL.value
        if path == self.storage.workspace_memory_path():
            return MemoryScope.WORKSPACE.value
        return "session"
