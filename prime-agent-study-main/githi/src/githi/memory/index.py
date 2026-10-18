"""The SQLite index (port of Grok's `index.rs`).

Disposable by design: this file can be deleted at any time and rebuilt from
the markdown, which is what makes destructive operations here safe.

The mechanism that makes re-indexing cheap is **content hashing**. On every
write the file is re-chunked and each chunk's hash compared with what is
stored; unchanged chunks are left completely alone. Without it, saving a
one-line edit to `MEMORY.md` would delete and re-insert every chunk in the
file — and once embeddings exist, re-embed them, turning a trivial edit into
a paid API call over the whole corpus.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from .chunker import chunk_markdown
from .schema import BASE_SCHEMA, GET_META, SCHEMA_VERSION, UPSERT_META, vec_schema


@dataclass
class IndexResult:
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    removed: int = 0


@dataclass
class IndexedChunk:
    chunk_id: str
    path: str
    text: str
    source: str
    start_line: int
    end_line: int
    created_at: int
    access_count: int
    last_accessed: int | None
    score: float = 0.0


class MemoryIndex:
    def __init__(self, db_path: Path, dimensions: int = 0) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path))
        self._conn.row_factory = sqlite3.Row
        self.dimensions = dimensions
        self.vector_available = self._load_vec_extension() if dimensions else False

        self._conn.executescript(BASE_SCHEMA)
        if self.vector_available:
            self._conn.executescript(vec_schema(dimensions))
        self._conn.execute(UPSERT_META, ("schema_version", str(SCHEMA_VERSION)))
        if dimensions:
            self._conn.execute(UPSERT_META, ("dimensions", str(dimensions)))
        self._conn.commit()

    def _load_vec_extension(self) -> bool:
        """Try to load sqlite-vec; report failure rather than raising.

        Grok's stated degradation path: without the extension, search falls
        back to FTS-only. Treating an absent optional extension as fatal
        would make the whole memory unusable on any machine where a
        native `.so` did not install.
        """
        try:
            import sqlite_vec

            self._conn.enable_load_extension(True)
            sqlite_vec.load(self._conn)
            self._conn.enable_load_extension(False)
            return True
        except Exception:  # noqa: BLE001 - optional acceleration, never required
            return False

    def close(self) -> None:
        self._conn.close()

    # ---------------- meta ----------------

    def get_meta(self, key: str) -> str | None:
        row = self._conn.execute(GET_META, (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self._conn.execute(UPSERT_META, (key, value))
        self._conn.commit()

    def table_names(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
        ).fetchall()
        return [r["name"] for r in rows]

    def chunk_count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]

    def chunk_rowids(self) -> list[int]:
        return [r["rowid"] for r in
                self._conn.execute("SELECT rowid FROM chunks ORDER BY rowid")]

    # ---------------- indexing ----------------

    def index_file(self, path: str, content: str, source: str) -> IndexResult:
        """Re-index one file, touching only what actually changed."""
        chunks = chunk_markdown(content, path=path)
        now = int(time.time())
        result = IndexResult()

        existing = {
            row["id"]: row for row in self._conn.execute(
                "SELECT rowid, id, hash FROM chunks WHERE path = ?", (path,))
        }
        seen: set[str] = set()

        for chunk in chunks:
            seen.add(chunk.id)
            prior = existing.get(chunk.id)

            if prior is not None and prior["hash"] == chunk.hash:
                result.unchanged += 1
                continue                       # the whole point: no work

            if prior is not None:
                self._delete_rowids([prior["rowid"]])
                result.updated += 1
            else:
                result.inserted += 1

            cursor = self._conn.execute(
                "INSERT INTO chunks(id, path, start_line, end_line, text, hash, "
                "source, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (chunk.id, chunk.path, chunk.start_line, chunk.end_line,
                 chunk.text, chunk.hash, source, now, now),
            )
            self._conn.execute(
                "INSERT INTO chunks_fts(rowid, text) VALUES (?, ?)",
                (cursor.lastrowid, chunk.text),
            )

        # Chunks whose content vanished from the file. Left in place they stay
        # searchable forever, so memory keeps asserting what the file no
        # longer says.
        stale = [row["rowid"] for cid, row in existing.items() if cid not in seen]
        if stale:
            self._delete_rowids(stale)
            result.removed = len(stale)

        self._conn.commit()
        return result

    def delete_path(self, path: str) -> int:
        rowids = [r["rowid"] for r in
                  self._conn.execute("SELECT rowid FROM chunks WHERE path = ?", (path,))]
        self._delete_rowids(rowids)
        self._conn.commit()
        return len(rowids)

    def _delete_rowids(self, rowids: list[int]) -> None:
        """Delete from both tables, always.

        `chunks_fts` is contentless, so SQLite will not cascade: dropping the
        `chunks` row alone leaves a posting that still matches queries and
        returns a hit whose text no longer exists.
        """
        for rowid in rowids:
            self._conn.execute(
                "INSERT INTO chunks_fts(chunks_fts, rowid, text) "
                "VALUES ('delete', ?, (SELECT text FROM chunks WHERE rowid = ?))",
                (rowid, rowid),
            )
            if self.vector_available:
                self._conn.execute(
                    "DELETE FROM chunks_vec WHERE chunk_id = "
                    "(SELECT id FROM chunks WHERE rowid = ?)", (rowid,))
            self._conn.execute("DELETE FROM chunks WHERE rowid = ?", (rowid,))

    # ---------------- retrieval ----------------

    def search_fts(self, query: str, limit: int = 50) -> list[IndexedChunk]:
        match = _fts_query(query)
        if not match:
            return []
        try:
            rows = self._conn.execute(
                "SELECT c.*, bm25(chunks_fts) AS rank FROM chunks_fts "
                "JOIN chunks c ON c.rowid = chunks_fts.rowid "
                "WHERE chunks_fts MATCH ? ORDER BY rank LIMIT ?",
                (match, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            # A malformed FTS5 expression is a user-input problem, not a bug:
            # search returns nothing rather than taking the run down.
            return []
        return [_to_chunk(r, score=-float(r["rank"])) for r in rows]

    def record_access(self, chunk_ids: list[str]) -> None:
        now = int(time.time())
        self._conn.executemany(
            "UPDATE chunks SET access_count = access_count + 1, last_accessed = ? "
            "WHERE id = ?",
            [(now, cid) for cid in chunk_ids],
        )
        self._conn.commit()

    def all_chunks(self) -> list[IndexedChunk]:
        return [_to_chunk(r) for r in self._conn.execute("SELECT * FROM chunks")]

    # ---------------- vectors ----------------

    def chunks_without_embeddings(self) -> list[tuple[str, str]]:
        """`(chunk_id, text)` for chunks that have no vector yet.

        This query is what keeps embedding incremental. Without it every
        write would re-embed the whole corpus.
        """
        if not self.vector_available:
            return []
        rows = self._conn.execute(
            "SELECT c.id, c.text FROM chunks c "
            "WHERE NOT EXISTS (SELECT 1 FROM chunks_vec v WHERE v.chunk_id = c.id)"
        ).fetchall()
        return [(r["id"], r["text"]) for r in rows]

    def upsert_embedding(self, chunk_id: str, vector: list[float]) -> bool:
        if not self.vector_available or len(vector) != self.dimensions:
            return False
        payload = json.dumps(list(vector))
        self._conn.execute("DELETE FROM chunks_vec WHERE chunk_id = ?", (chunk_id,))
        self._conn.execute(
            "INSERT INTO chunks_vec(chunk_id, embedding) VALUES (?, ?)",
            (chunk_id, payload),
        )
        self._conn.commit()
        return True

    def get_embedding(self, chunk_id: str) -> list[float] | None:
        if not self.vector_available:
            return None
        row = self._conn.execute(
            "SELECT embedding FROM chunks_vec WHERE chunk_id = ?", (chunk_id,)
        ).fetchone()
        if row is None:
            return None
        raw = row["embedding"]
        return json.loads(raw) if isinstance(raw, str) else list(raw)

    def search_knn(self, query: list[float], limit: int = 20) -> list[IndexedChunk]:
        """K-nearest neighbours over stored vectors.

        vec0 returns L2 `distance`; it is converted to a similarity in
        [0, 1] so it can be merged with the normalised FTS score. Unit-length
        vectors bound L2 distance at 2, hence the `1 - d/2` mapping.
        """
        if not self.vector_available or len(query) != self.dimensions:
            return []
        try:
            rows = self._conn.execute(
                "SELECT c.*, v.distance AS distance FROM chunks_vec v "
                "JOIN chunks c ON c.id = v.chunk_id "
                "WHERE v.embedding MATCH ? AND k = ? ORDER BY v.distance",
                (json.dumps(list(query)), limit),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        return [_to_chunk(r, score=max(0.0, 1.0 - float(r["distance"]) / 2.0))
                for r in rows]


_FTS_TOKEN = re.compile(r"[A-Za-z0-9_]+")


def _fts_query(raw: str) -> str:
    """Rewrite arbitrary user text into a safe FTS5 expression.

    FTS5 has its own grammar — quotes, `AND`/`OR`/`NOT`, `*`, parentheses —
    and user text reaches it directly. An unbalanced quote is a syntax error
    that would otherwise kill search, so the query is reduced to bare tokens
    OR-ed together. Precision is lost; availability is not.
    """
    tokens = _FTS_TOKEN.findall(raw)
    keywords = {"AND", "OR", "NOT", "NEAR"}
    tokens = [t for t in tokens if t.upper() not in keywords]
    return " OR ".join(f'"{t}"' for t in tokens)


def _to_chunk(row: sqlite3.Row, score: float = 0.0) -> IndexedChunk:
    return IndexedChunk(
        chunk_id=row["id"], path=row["path"], text=row["text"], source=row["source"],
        start_line=row["start_line"], end_line=row["end_line"],
        created_at=row["created_at"], access_count=row["access_count"],
        last_accessed=row["last_accessed"], score=score,
    )
