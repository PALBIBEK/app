"""Markdown-aware chunking (port of Grok's `chunker.rs`).

Two properties carry the whole design:

* **Structure is respected.** Splitting mid-code-block or mid-table produces
  chunks that are individually meaningless, and a retrieved fragment of a
  fenced block is worse than no result — the model cannot tell it is looking
  at half of something.
* **Chunks are self-contained.** A retrieved chunk arrives with no
  surrounding document, so ancestor headers are carried into it. Without
  that, "use `--depth 100`" retrieved on its own has lost the section that
  said which command it belongs to.
"""

from __future__ import annotations

from githi.memory.chunker import Chunk, chunk_markdown

DOC = """\
# Project Notes

Intro paragraph about the project.

## API Limits

The search API allows 30 requests per minute.
Core REST allows 5000 per hour.

### Pagination

Use per_page=100; the default of 30 triples your call count.

## Cloning

A full clone of langchain takes about 190 seconds.

```bash
git clone https://github.com/langchain-ai/langchain.git
git log -S "strict = True" -- path/to/file.py
```

Shallow clones break `git blame` attribution.
"""


class TestBasics:
    def test_produces_chunks(self):
        assert len(chunk_markdown(DOC, path="MEMORY.md")) >= 2

    def test_chunks_carry_source_path(self):
        assert all(c.path == "MEMORY.md" for c in chunk_markdown(DOC, path="MEMORY.md"))

    def test_line_ranges_are_sane(self):
        for c in chunk_markdown(DOC, path="m.md"):
            assert 1 <= c.start_line <= c.end_line

    def test_no_content_is_lost(self):
        """Every distinctive sentence must survive into some chunk —
        silently dropping content is the worst possible chunker bug because
        nothing ever reports it."""
        joined = "\n".join(c.text for c in chunk_markdown(DOC, path="m.md"))
        for phrase in ("30 requests per minute", "5000 per hour",
                       "per_page=100", "190 seconds",
                       "break `git blame` attribution"):
            assert phrase in joined, phrase

    def test_empty_document(self):
        assert chunk_markdown("", path="m.md") == []

    def test_whitespace_only_document(self):
        assert chunk_markdown("\n\n   \n", path="m.md") == []

    def test_document_without_headers_still_chunks(self):
        assert len(chunk_markdown("Just a paragraph.\n\nAnd another.\n", path="m.md")) >= 1


class TestSelfContainment:
    def test_chunks_carry_ancestor_headers(self):
        """The retrieval-critical property: a chunk about pagination must
        still say it belongs to API Limits, because the reader gets the chunk
        and nothing else."""
        chunks = chunk_markdown(DOC, path="m.md")
        pagination = next(c for c in chunks if "per_page=100" in c.text)
        assert "Pagination" in pagination.text
        assert "API Limits" in pagination.text, "lost the parent section"

    def test_top_level_title_is_carried(self):
        chunks = chunk_markdown(DOC, path="m.md")
        cloning = next(c for c in chunks if "190 seconds" in c.text)
        assert "Project Notes" in cloning.text

    def test_sibling_headers_do_not_leak(self):
        """Ancestors only. A chunk under 'Cloning' must not claim to be
        under 'API Limits' — that would make retrieval attribute a fact to
        the wrong section."""
        chunks = chunk_markdown(DOC, path="m.md")
        cloning = next(c for c in chunks if "190 seconds" in c.text)
        assert "API Limits" not in cloning.text


class TestCodeBlocks:
    def test_fenced_block_is_never_split(self):
        """A half-fence is unparseable and actively misleading: the model
        cannot tell the command is truncated."""
        for c in chunk_markdown(DOC, path="m.md"):
            assert c.text.count("```") % 2 == 0, f"unbalanced fence in: {c.text[:80]}"

    def test_code_content_survives_intact(self):
        joined = "\n".join(c.text for c in chunk_markdown(DOC, path="m.md"))
        assert 'git log -S "strict = True"' in joined

    def test_headers_inside_code_blocks_are_not_treated_as_headers(self):
        doc = "# Real\n\n```bash\n# not a header, a shell comment\necho hi\n```\n"
        chunks = chunk_markdown(doc, path="m.md")
        assert len(chunks) == 1, "split on a comment inside a fence"


class TestSizing:
    def test_long_section_is_split(self):
        doc = "# Title\n\n" + "\n\n".join(f"Paragraph number {i}. " * 30 for i in range(40))
        chunks = chunk_markdown(doc, path="m.md", max_chars=1200)
        assert len(chunks) > 1
        assert all(len(c.text) <= 1200 * 2 for c in chunks), "cap wildly exceeded"

    def test_split_section_keeps_its_header_on_every_piece(self):
        doc = "# Title\n\n## Section\n\n" + "\n\n".join(f"Para {i}. " * 30 for i in range(40))
        for c in chunk_markdown(doc, path="m.md", max_chars=1200):
            assert "Section" in c.text, "a split piece lost its header"

    def test_oversized_indivisible_block_is_kept_whole(self):
        """A single code block larger than the cap cannot be split without
        corrupting it, so it is emitted intact and over-size on purpose."""
        big = "# T\n\n```\n" + ("x" * 5000) + "\n```\n"
        chunks = chunk_markdown(big, path="m.md", max_chars=500)
        assert len(chunks) == 1 and chunks[0].text.count("```") == 2

    def test_tiny_trailing_fragments_are_merged(self):
        """A 20-character chunk retrieves badly and costs a full row; it
        belongs with its neighbour."""
        doc = "# T\n\n" + "Long paragraph. " * 60 + "\n\nok\n"
        assert all(len(c.text.strip()) > 40 for c in chunk_markdown(doc, path="m.md", max_chars=900))


class TestChunkIdentity:
    def test_ids_are_stable_across_runs(self):
        """Re-indexing an unchanged file must not churn every row."""
        a = chunk_markdown(DOC, path="m.md")
        b = chunk_markdown(DOC, path="m.md")
        assert [c.id for c in a] == [c.id for c in b]

    def test_ids_differ_between_files(self):
        a = chunk_markdown(DOC, path="one.md")
        b = chunk_markdown(DOC, path="two.md")
        assert set(c.id for c in a).isdisjoint(c.id for c in b)

    def test_hash_changes_when_content_changes(self):
        """The hash is what lets the index skip unchanged chunks instead of
        re-embedding the whole corpus on every write."""
        a = chunk_markdown("# T\n\noriginal\n", path="m.md")[0]
        b = chunk_markdown("# T\n\nedited\n", path="m.md")[0]
        assert a.hash != b.hash

    def test_hash_is_stable_for_identical_content(self):
        a = chunk_markdown(DOC, path="m.md")
        b = chunk_markdown(DOC, path="m.md")
        assert [c.hash for c in a] == [c.hash for c in b]


def test_chunk_dataclass_fields():
    c = chunk_markdown(DOC, path="m.md")[0]
    assert isinstance(c, Chunk)
    for field in ("id", "path", "start_line", "end_line", "text", "hash"):
        assert getattr(c, field) is not None
