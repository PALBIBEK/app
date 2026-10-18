"""The SQLite index (port of Grok's `schema.rs` + `index.rs`).

The index is **disposable**: delete `index.sqlite3` and it rebuilds from the
markdown. Nothing here is a system of record, which is what makes aggressive
behaviour (dropping on schema change, purging on file deletion) safe.

The property that makes indexing cheap enough to run on every write is
content hashing: a chunk whose blake2b hash is unchanged is left alone rather
than deleted and re-inserted, so re-indexing an untouched file does no work
and — once embeddings exist — costs no tokens.
"""

from __future__ import annotations

import pytest

from githi.memory.index import MemoryIndex

DOC_A = """\
# API Limits

The search API allows 30 requests per minute.

## Pagination

Use per_page=100 to avoid tripling your call count.
"""

DOC_B = """\
# Cloning

A full clone takes about 190 seconds; shallow clones break git blame.
"""


@pytest.fixture
def index(tmp_path):
    idx = MemoryIndex(tmp_path / "index.sqlite3")
    yield idx
    idx.close()


class TestSchema:
    def test_creates_tables(self, index):
        names = index.table_names()
        assert {"meta", "chunks", "chunks_fts"} <= set(names)

    def test_records_schema_version(self, index):
        assert index.get_meta("schema_version") is not None

    def test_reopening_is_idempotent(self, tmp_path):
        MemoryIndex(tmp_path / "i.db").close()
        second = MemoryIndex(tmp_path / "i.db")
        assert {"meta", "chunks", "chunks_fts"} <= set(second.table_names())
        second.close()

    def test_creates_parent_directory(self, tmp_path):
        MemoryIndex(tmp_path / "deep" / "nested" / "i.db").close()
        assert (tmp_path / "deep" / "nested" / "i.db").exists()


class TestIndexing:
    def test_index_file_stores_chunks(self, index):
        index.index_file("MEMORY.md", DOC_A, source="workspace")
        assert index.chunk_count() >= 1

    def test_chunks_are_searchable_by_keyword(self, index):
        index.index_file("MEMORY.md", DOC_A, source="workspace")
        hits = index.search_fts("pagination")
        assert hits and any("per_page" in h.text for h in hits)

    def test_search_returns_source_and_path(self, index):
        index.index_file("MEMORY.md", DOC_A, source="workspace")
        hit = index.search_fts("requests")[0]
        assert hit.path == "MEMORY.md" and hit.source == "workspace"

    def test_no_match_returns_empty(self, index):
        index.index_file("MEMORY.md", DOC_A, source="workspace")
        assert index.search_fts("kubernetes") == []

    def test_multiple_files_coexist(self, index):
        index.index_file("a.md", DOC_A, source="workspace")
        index.index_file("b.md", DOC_B, source="session")
        assert {h.path for h in index.search_fts("clone OR requests")} <= {"a.md", "b.md"}
        assert index.search_fts("190")


class TestChangeDetection:
    def test_reindexing_unchanged_content_is_a_no_op(self, index):
        """The cheapness property. If unchanged chunks churned, every write
        would re-embed the whole corpus."""
        index.index_file("MEMORY.md", DOC_A, source="workspace")
        first = index.chunk_rowids()
        result = index.index_file("MEMORY.md", DOC_A, source="workspace")
        assert index.chunk_rowids() == first, "rows were replaced"
        assert result.unchanged > 0 and result.inserted == 0

    def test_edited_content_is_reindexed(self, index):
        index.index_file("MEMORY.md", DOC_A, source="workspace")
        edited = DOC_A.replace("30 requests", "60 requests")
        index.index_file("MEMORY.md", edited, source="workspace")
        assert index.search_fts("60"), "new value not searchable"
        assert not index.search_fts("30"), "old value still searchable"

    def test_queries_are_or_semantics_not_phrase(self):
        """Pinned deliberately. `_fts_query` reduces user text to bare tokens
        OR-ed together, because FTS5's grammar (quotes, AND/OR/NOT, `*`)
        would otherwise turn a stray apostrophe into a syntax error that
        kills search. The trade is precision: "30 requests" matches a chunk
        containing only "requests". Ranking, not filtering, is what
        distinguishes those — see the scoring layer."""
        from githi.memory.index import _fts_query

        assert _fts_query("30 requests") == '"30" OR "requests"'
        assert _fts_query('"unterminated') == '"unterminated"'
        assert _fts_query("AND OR NOT") == "", "bare operators must not reach FTS5"

    def test_removed_sections_are_purged(self, index):
        """Stale chunks from deleted content stay searchable forever unless
        removed — the memory would keep asserting things the file no longer
        says."""
        index.index_file("MEMORY.md", DOC_A, source="workspace")
        index.index_file("MEMORY.md", "# API Limits\n\nkept only\n", source="workspace")
        assert not index.search_fts("pagination")
        assert index.search_fts("kept")

    def test_delete_path_removes_all_its_chunks(self, index):
        index.index_file("a.md", DOC_A, source="workspace")
        index.index_file("b.md", DOC_B, source="session")
        index.delete_path("a.md")
        assert not index.search_fts("pagination")
        assert index.search_fts("190"), "deleted the wrong file's chunks"

    def test_delete_path_for_unknown_file_is_harmless(self, index):
        index.delete_path("never-indexed.md")

    def test_fts_stays_in_sync_after_delete(self, index):
        """`chunks_fts` is contentless, so a row deleted from `chunks`
        without the matching FTS delete leaves a ghost that still matches."""
        index.index_file("a.md", DOC_A, source="workspace")
        index.delete_path("a.md")
        assert index.search_fts("pagination") == []
        assert index.chunk_count() == 0


class TestAccessTracking:
    def test_access_count_starts_at_zero(self, index):
        index.index_file("a.md", DOC_A, source="workspace")
        assert index.search_fts("requests")[0].access_count == 0

    def test_record_access_increments(self, index):
        index.index_file("a.md", DOC_A, source="workspace")
        chunk_id = index.search_fts("requests")[0].chunk_id
        index.record_access([chunk_id])
        assert index.search_fts("requests")[0].access_count == 1

    def test_record_access_sets_last_accessed(self, index):
        index.index_file("a.md", DOC_A, source="workspace")
        chunk_id = index.search_fts("requests")[0].chunk_id
        index.record_access([chunk_id])
        assert index.search_fts("requests")[0].last_accessed is not None

    def test_record_access_is_safe_for_unknown_ids(self, index):
        index.record_access(["nonexistent"])


class TestRobustness:
    def test_fts_special_characters_do_not_crash(self, index):
        """User text reaches FTS5 directly; unescaped quotes or operators are
        a syntax error that would take down search entirely."""
        index.index_file("a.md", DOC_A, source="workspace")
        for query in ['"unterminated', "AND OR NOT", "foo*bar", "(unclosed", "a'b"]:
            index.search_fts(query)

    def test_empty_query_returns_empty(self, index):
        index.index_file("a.md", DOC_A, source="workspace")
        assert index.search_fts("") == []

    def test_indexing_empty_content_stores_nothing(self, index):
        index.index_file("empty.md", "", source="workspace")
        assert index.chunk_count() == 0

    def test_limit_is_respected(self, index):
        index.index_file("a.md", "\n\n".join(f"## S{i}\n\nrepeated word here {i}"
                                             for i in range(30)), source="workspace")
        assert len(index.search_fts("repeated", limit=5)) <= 5


def test_index_survives_process_restart(tmp_path):
    first = MemoryIndex(tmp_path / "i.db")
    first.index_file("a.md", DOC_A, source="workspace")
    first.close()

    second = MemoryIndex(tmp_path / "i.db")
    assert second.search_fts("pagination")
    second.close()
