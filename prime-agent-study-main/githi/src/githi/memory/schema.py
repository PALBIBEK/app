"""SQL schema for the memory index (port of Grok's `schema.rs`).

Three tables always, a fourth when vector search is available:

* ``meta``       — key/value (schema version, embedding dimensions)
* ``chunks``     — indexed text with a content hash
* ``chunks_fts`` — **contentless** FTS5 mirror for BM25
* ``chunks_vec`` — vec0 virtual table for KNN (only if the extension loads)

``chunks_fts`` is declared ``content=''`` so FTS5 stores only the inverted
index and not a second copy of every chunk. The cost is that it will not
delete rows for you: every ``DELETE FROM chunks`` needs a matching FTS delete
or the orphaned posting keeps matching queries forever, returning a hit whose
text no longer exists.
"""

from __future__ import annotations

SCHEMA_VERSION = 1

BASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
    rowid         INTEGER PRIMARY KEY AUTOINCREMENT,
    id            TEXT UNIQUE NOT NULL,
    path          TEXT NOT NULL,
    start_line    INTEGER NOT NULL,
    end_line      INTEGER NOT NULL,
    text          TEXT NOT NULL,
    hash          TEXT NOT NULL,
    source        TEXT NOT NULL,
    created_at    INTEGER NOT NULL,
    updated_at    INTEGER NOT NULL,
    access_count  INTEGER NOT NULL DEFAULT 0,
    last_accessed INTEGER
);

CREATE INDEX IF NOT EXISTS idx_chunks_path   ON chunks(path);
CREATE INDEX IF NOT EXISTS idx_chunks_hash   ON chunks(hash);
CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(source);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text, content='');
"""


def vec_schema(dimensions: int) -> str:
    return (
        "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vec USING vec0(\n"
        "    chunk_id TEXT PRIMARY KEY,\n"
        f"    embedding FLOAT[{dimensions}]\n"
        ");\n"
    )


UPSERT_META = "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)"
GET_META = "SELECT value FROM meta WHERE key = ?"
