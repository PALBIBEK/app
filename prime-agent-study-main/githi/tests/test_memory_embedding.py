"""Embeddings and hybrid FTS+KNN retrieval (ports of `embedding.rs` and the
vector half of `index.rs`/`search.rs`).

Every test here is offline, driven by a deterministic fake provider. A memory
layer whose tests need a live API and a funded key is one nobody runs, and
the interesting logic — batching, merge weighting, graceful degradation — is
entirely independent of who computes the vectors.

The property that matters most is **graceful degradation**: Grok is explicit
that without sqlite-vec or embeddings, search falls back to FTS-only with
`text_weight = 1.0`. An agent whose memory hard-fails when an embedding
provider is down is worse than one that searches by keyword.
"""

from __future__ import annotations

import math

import pytest

from githi.memory.embedding import (
    EmbeddingProvider,
    HashingEmbeddingProvider,
    NullEmbeddingProvider,
    embed_missing_chunks,
)
from githi.memory.index import MemoryIndex
from githi.memory.search import SearchConfig, search


class RecordingProvider(EmbeddingProvider):
    """Deterministic vectors, and a record of how it was called."""

    def __init__(self, dimensions: int = 8, max_batch_size: int = 3) -> None:
        self._dims = dimensions
        self.max_batch_size = max_batch_size
        self.batches: list[list[str]] = []

    @property
    def dimensions(self) -> int:
        return self._dims

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        self.batches.append(list(texts))
        return [HashingEmbeddingProvider(self._dims).embed_one(t) for t in texts]


@pytest.fixture
def index(tmp_path):
    idx = MemoryIndex(tmp_path / "i.db", dimensions=8)
    yield idx
    idx.close()


class TestProviders:
    def test_hashing_provider_is_deterministic(self):
        provider = HashingEmbeddingProvider(dimensions=8)
        assert provider.embed_batch(["hello"]) == provider.embed_batch(["hello"])

    def test_hashing_provider_differs_by_text(self):
        provider = HashingEmbeddingProvider(dimensions=8)
        a, b = provider.embed_batch(["alpha", "beta"])
        assert a != b

    def test_vectors_are_unit_length(self):
        """Cosine similarity reduces to a dot product only if vectors are
        normalised; skipping it makes long chunks score higher for being
        long."""
        vector = HashingEmbeddingProvider(dimensions=16).embed_batch(["some text"])[0]
        assert math.isclose(math.sqrt(sum(x * x for x in vector)), 1.0, rel_tol=1e-6)

    def test_dimensions_are_respected(self):
        assert len(HashingEmbeddingProvider(dimensions=32).embed_batch(["x"])[0]) == 32

    def test_null_provider_returns_nothing(self):
        """The explicit 'no embeddings' path, so callers need no None checks."""
        null = NullEmbeddingProvider()
        assert null.embed_batch(["a", "b"]) == []
        assert null.dimensions == 0


class TestBatching:
    def test_texts_are_split_into_batches(self, index):
        # Bodies must clear MIN_CHUNK_CHARS or `_merge_tiny` folds them into
        # a single chunk and there is nothing to batch.
        index.index_file("a.md", "\n\n".join(
            f"## S{i}\n\nA sufficiently long body for section {i} so the chunker "
            f"keeps it as its own chunk rather than merging it." for i in range(7)),
            source="workspace")
        provider = RecordingProvider(max_batch_size=3)
        embed_missing_chunks(index, provider)
        assert len(provider.batches) >= 3
        assert all(len(b) <= 3 for b in provider.batches)

    def test_only_unembedded_chunks_are_sent(self, index):
        """The cost property: re-running must not re-embed the whole corpus,
        or every write becomes a paid pass over everything."""
        index.index_file("a.md", "# A\n\nbody text\n", source="workspace")
        provider = RecordingProvider()
        assert embed_missing_chunks(index, provider) > 0

        provider.batches.clear()
        assert embed_missing_chunks(index, provider) == 0
        assert provider.batches == [], "re-embedded already-embedded chunks"

    def test_edited_chunk_is_re_embedded(self, index):
        index.index_file("a.md", "# A\n\noriginal body\n", source="workspace")
        provider = RecordingProvider()
        embed_missing_chunks(index, provider)

        index.index_file("a.md", "# A\n\nedited body\n", source="workspace")
        assert embed_missing_chunks(index, provider) > 0

    def test_provider_failure_is_survivable(self, index):
        """A dead embedding endpoint must degrade to FTS-only, not take the
        run down."""
        class Broken(EmbeddingProvider):
            dimensions = 8

            def embed_batch(self, texts):
                raise RuntimeError("provider is down")

        index.index_file("a.md", "# A\n\nbody\n", source="workspace")
        assert embed_missing_chunks(index, Broken()) == 0
        assert search(index, "body"), "FTS search stopped working"


class TestVectorStorage:
    def test_embeddings_round_trip(self, index):
        index.index_file("a.md", "# A\n\nbody text\n", source="workspace")
        embed_missing_chunks(index, RecordingProvider())
        chunk_id = index.all_chunks()[0].chunk_id
        assert index.get_embedding(chunk_id) is not None

    def test_knn_finds_the_nearest_chunk(self, index):
        index.index_file("a.md", "# A\n\nalpha content here\n", source="workspace")
        index.index_file("b.md", "# B\n\nbeta different words\n", source="workspace")
        provider = HashingEmbeddingProvider(dimensions=8)
        embed_missing_chunks(index, provider)

        query = provider.embed_one("alpha content here")
        hits = index.search_knn(query, limit=2)
        assert hits and hits[0].path == "a.md"

    def test_knn_without_embeddings_returns_empty(self, index):
        index.index_file("a.md", "# A\n\nbody\n", source="workspace")
        assert index.search_knn([0.0] * 8, limit=3) == []

    def test_deleting_a_path_drops_its_vectors(self, index):
        """A vector left behind after its chunk is gone keeps matching KNN
        and returns a result whose text no longer exists."""
        index.index_file("a.md", "# A\n\nbody\n", source="workspace")
        embed_missing_chunks(index, RecordingProvider())
        index.delete_path("a.md")
        assert index.search_knn([0.1] * 8, limit=5) == []


class TestHybridMerge:
    def test_vector_only_match_is_retrievable(self, index):
        """The reason to have vectors at all: a semantically related chunk
        with no keyword overlap."""
        index.index_file("a.md", "# A\n\nrepository cloning duration\n", source="workspace")
        provider = HashingEmbeddingProvider(dimensions=8)
        embed_missing_chunks(index, provider)

        results = search(index, "zzz no keyword overlap zzz",
                         SearchConfig(query_embedding=provider.embed_one(
                             "repository cloning duration")))
        assert results and results[0].path == "a.md"

    def test_fts_only_chunk_is_not_penalised(self, index):
        """Grok is explicit: a chunk matching only FTS must not be scaled
        down to text_weight merely because *other* chunks have vectors."""
        provider = HashingEmbeddingProvider(dimensions=8)
        index.index_file("embedded.md", "# E\n\nsomething else entirely here\n",
                         source="workspace")
        embed_missing_chunks(index, provider)

        # Indexed *after* embedding, so this chunk genuinely has no vector --
        # which is the situation the rule is about.
        index.index_file("plain.md", "# P\n\nunique keyword zebra appears here\n",
                         source="workspace")
        assert index.get_embedding(
            next(c.chunk_id for c in index.all_chunks() if c.path == "plain.md")
        ) is None, "fixture failed to create an unembedded chunk"

        plain = search(index, "zebra")[0].score
        hybrid = search(index, "zebra", SearchConfig(
            query_embedding=provider.embed_one("completely unrelated")))[0].score
        assert hybrid == pytest.approx(plain, rel=0.05), (
            "an FTS-only chunk was scaled down because other chunks have vectors")

    def test_no_embedding_degrades_to_fts_only(self, index):
        index.index_file("a.md", "# A\n\nkeyword body\n", source="workspace")
        assert search(index, "keyword", SearchConfig(query_embedding=None))

    def test_both_signals_combine(self, index):
        index.index_file("a.md", "# A\n\nclone timing measurement\n", source="workspace")
        index.index_file("b.md", "# B\n\nclone timing measurement\n", source="workspace")
        provider = HashingEmbeddingProvider(dimensions=8)
        embed_missing_chunks(index, provider)

        results = search(index, "clone timing", SearchConfig(
            query_embedding=provider.embed_one("clone timing measurement")))
        assert len(results) == 2 and all(r.score > 0 for r in results)


def test_index_reports_whether_vectors_are_available(index):
    assert isinstance(index.vector_available, bool)
