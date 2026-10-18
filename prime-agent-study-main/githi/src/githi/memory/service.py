"""The memory module's front door: `recall` and `remember`.

Everything underneath -- storage, chunking, the SQLite index, BM25 scoring,
decay, MMR -- is an implementation detail. Callers get two verbs, because a
caller that has to know `index_file(path, content, source)` and that
`SearchResult` carries `snippet` rather than `text` will get it wrong, and the
agent has no business knowing any of it.

Three properties this layer is responsible for:

**Markdown is the truth; the index is a cache.** The database can be deleted,
corrupted, or left behind by a schema change, and the only cost is the time to
rebuild. That is checked on every open by comparing the file's mtime against
what was indexed, which also means a `MEMORY.md` edited by hand in a text
editor is picked up -- editability is the point of storing knowledge as
markdown rather than rows.

**Appending, never overwriting.** `MEMORY.md` is the only copy of what was
learned. A write that replaces it discards every earlier fact silently.

**Nothing here may end a run.** Memory is an enhancement. A corrupt index, an
unwritable disk or a malformed query degrades to "no memory this turn", never
to an exception -- the agent's actual work is not contingent on it.
"""

from __future__ import annotations

from pathlib import Path

from .index import MemoryIndex
from .search import SearchConfig, search
from .storage import MemoryScope, MemoryStorage

RECALL_HEADER = "Relevant notes recalled from memory (not part of the user's message):"

MIN_SCORE = 0.35
"""Below this, a hit is noise.

Precision matters more than recall here, which is the opposite of the usual
search trade-off. Injected memory costs tokens on *every* user turn, and a
store that returns its best guess for any query -- "the capital of France"
included -- trains the model to skip the section entirely. Better to recall
nothing than to be reliably irrelevant."""


class MemoryService:
    def __init__(self, root: Path | str, scope: MemoryScope = MemoryScope.GLOBAL,
                 min_score: float = MIN_SCORE) -> None:
        self.root = Path(root)
        self.scope = scope
        self.min_score = min_score
        self.storage = MemoryStorage(self.root)
        self._index: MemoryIndex | None = None

    # ---------------- paths ----------------

    @property
    def memory_path(self) -> Path:
        return self.storage.path_for(self.scope)

    @property
    def index_path(self) -> Path:
        return self.root / "index.db"

    def read(self) -> str:
        return self.storage.read(self.scope)

    # ---------------- the two verbs ----------------

    def remember(self, text: str, scope: MemoryScope | None = None) -> None:
        """Append a fact and reindex.

        Silently ignores blank input and exact duplicates. A duplicate is not
        an error -- the same lesson genuinely does get learned twice -- but
        storing it twice would let one fact outweigh others purely by
        repetition.
        """
        text = (text or "").strip()
        if not text:
            return

        scope = scope or self.scope
        existing = self.storage.read(scope)
        if text in existing:
            return

        body = existing.rstrip()
        merged = f"{body}\n\n- {text}\n" if body else f"# Memory\n\n- {text}\n"
        try:
            self.storage.write(scope, merged)
            self._reindex()
        except OSError:
            return                       # a full disk must not end the run

    def recall(self, query: str, limit: int = 5) -> list[str]:
        """The snippets worth showing for this query, best first."""
        query = (query or "").strip()
        if not query:
            return []
        try:
            index = self._open()
            results = search(index, query, SearchConfig(
                max_results=limit, min_score=self.min_score))
            return [r.snippet for r in results][:limit]
        except Exception:  # noqa: BLE001 - see the module docstring
            return []

    def recall_block(self, query: str, limit: int = 5) -> str:
        """`recall`, formatted for injection into a prompt.

        Empty when nothing matches, so a caller can append it unconditionally
        and stay silent. The header is not decoration: without it the model
        reads recalled text as something the user just said, and will
        attribute a remembered fact back to them.
        """
        hits = self.recall(query, limit)
        if not hits:
            return ""
        lines = [RECALL_HEADER]
        lines += [f"- {h.strip()}" for h in hits]
        return "\n".join(lines)

    # ---------------- index lifecycle ----------------

    def _open(self) -> MemoryIndex:
        if self._index is None:
            try:
                self._index = MemoryIndex(self.index_path)
            except Exception:  # noqa: BLE001 - corrupt db: start over rather than fail
                self.index_path.unlink(missing_ok=True)
                self._index = MemoryIndex(self.index_path)
            self._refresh_if_stale()
        return self._index

    def _refresh_if_stale(self) -> None:
        """Reindex when `MEMORY.md` is newer than what we indexed.

        This is what makes hand-editing work, and what makes a deleted index
        self-healing. Comparing mtimes rather than hashing keeps the check
        cheap enough to run on every open.
        """
        if not self.memory_path.is_file():
            return
        stamp = str(self.memory_path.stat().st_mtime)
        if self._index and self._index.get_meta("memory_mtime") == stamp:
            return
        self._reindex(stamp)

    def _reindex(self, stamp: str | None = None) -> None:
        if not self.memory_path.is_file():
            return
        try:
            index = self._index or MemoryIndex(self.index_path)
            self._index = index
            index.index_file(str(self.memory_path), self.read(), self.scope.value)
            index.set_meta("memory_mtime",
                           stamp or str(self.memory_path.stat().st_mtime))
        except Exception:  # noqa: BLE001 - an unindexable store is an empty one
            return

    def close(self) -> None:
        if self._index is not None:
            self._index.close()
            self._index = None
