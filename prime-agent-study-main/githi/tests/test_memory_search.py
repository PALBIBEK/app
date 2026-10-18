"""The scoring pipeline (port of Grok's `search.rs`).

Grok's documented order, which this follows:

1. FTS5 keyword search (always available)
2. Vector KNN (when embeddings exist)          -- later phase
3. Merge by chunk_id, normalise scores to [0,1]
4. Skip content-free chunks (scaffold stubs)
5. Temporal decay -- **session chunks only**; curated memory is evergreen
6. Source weights + access-frequency boost, filter by min_score
7. MMR diversity re-rank                        -- later phase
8. Limit to max_results

Stage 5 is the one that carries a real design opinion: a fact a human
curated into MEMORY.md should not fade just because it is old, while a raw
session log should. Decaying both equally would slowly bury the good stuff
under whatever happened most recently.
"""

from __future__ import annotations

import time

import pytest

from githi.memory.index import MemoryIndex
from githi.memory.search import SearchConfig, search

DAY = 86_400


@pytest.fixture
def index(tmp_path):
    idx = MemoryIndex(tmp_path / "i.db")
    yield idx
    idx.close()


def _age(index, chunk_id: str, days: float) -> None:
    """Backdate a chunk so decay has something to bite on."""
    index._conn.execute("UPDATE chunks SET created_at = ? WHERE id = ?",
                        (int(time.time() - days * DAY), chunk_id))
    index._conn.commit()


class TestBasics:
    def test_finds_a_match(self, index):
        index.index_file("MEMORY.md", "# API\n\nrate limit is 60 per hour\n",
                         source="workspace")
        assert search(index, "rate limit")

    def test_no_match_returns_empty(self, index):
        index.index_file("MEMORY.md", "# API\n\nrate limit\n", source="workspace")
        assert search(index, "kubernetes deployment") == []

    def test_empty_query_returns_empty(self, index):
        index.index_file("MEMORY.md", "# API\n\nrate limit\n", source="workspace")
        assert search(index, "") == []

    def test_results_carry_provenance(self, index):
        index.index_file("MEMORY.md", "# API\n\nrate limit\n", source="workspace")
        hit = search(index, "rate")[0]
        assert hit.path == "MEMORY.md" and hit.source == "workspace"
        assert hit.snippet and hit.chunk_id

    def test_results_are_ordered_by_score(self, index):
        index.index_file("a.md", "# A\n\nclone timing clone timing clone\n", source="session")
        index.index_file("b.md", "# B\n\nunrelated text about clone\n", source="session")
        results = search(index, "clone timing")
        assert results == sorted(results, key=lambda r: r.score, reverse=True)

    def test_max_results_is_respected(self, index):
        body = "\n\n".join(f"## S{i}\n\nrepeated token here" for i in range(30))
        index.index_file("a.md", body, source="workspace")
        assert len(search(index, "repeated", SearchConfig(max_results=3))) <= 3


class TestNormalisation:
    def test_scores_are_clamped_to_unit_range(self, index):
        """Displayed scores must be comparable across queries; raw BM25 is
        an unbounded, corpus-relative number."""
        index.index_file("a.md", "# A\n\nrate limit rate limit rate\n", source="workspace")
        for hit in search(index, "rate limit"):
            assert 0.0 <= hit.score <= 1.0

    def test_single_result_is_not_degenerate(self, index):
        """With one hit, min == max; naive min-max normalisation gives 0/0
        and would silently score the only answer at zero."""
        index.index_file("a.md", "# A\n\nsolitary unique token\n", source="workspace")
        hit = search(index, "solitary")[0]
        assert hit.score > 0.0


class TestContentFreeFiltering:
    def test_scaffold_stub_never_appears(self, index):
        """The auto-generated MEMORY.md stub matches keyword queries and is
        pure noise -- returning it wastes a slot and tells the model
        nothing."""
        index.index_file("MEMORY.md",
                         "# Memory\n\nAdd project-specific knowledge here.\n",
                         source="workspace")
        assert search(index, "project knowledge") == []

    def test_session_chunk_quoting_a_marker_is_kept(self, index):
        """Grok scopes the scaffold check to evergreen sources for exactly
        this reason: a session log that happens to quote the stub's wording
        is real content."""
        index.index_file("s.md",
                         "# Log\n\nThe file said 'Add project-specific knowledge "
                         "here.' so I populated it with the real rate limits.\n",
                         source="session")
        assert search(index, "project knowledge")

    def test_structurally_empty_chunk_is_dropped(self, index):
        index.index_file("MEMORY.md", "# Heading Only\n\n##\n", source="workspace")
        assert search(index, "Heading") == []


class TestTemporalDecay:
    def test_old_session_chunk_scores_below_a_fresh_one(self, index):
        index.index_file("old.md", "# Old\n\nclone timing note\n", source="session")
        index.index_file("new.md", "# New\n\nclone timing note\n", source="session")
        old_id = next(c.chunk_id for c in index.all_chunks() if c.path == "old.md")
        _age(index, old_id, days=120)

        results = {r.path: r.score for r in search(index, "clone timing")}
        assert results["new.md"] > results["old.md"]

    def test_curated_memory_is_evergreen(self, index):
        """A fact someone curated into MEMORY.md must not fade with age --
        otherwise long-lived knowledge is slowly buried by recent noise."""
        index.index_file("MEMORY.md", "# Facts\n\nclone timing note\n", source="workspace")
        chunk_id = index.all_chunks()[0].chunk_id
        fresh = search(index, "clone timing")[0].score
        _age(index, chunk_id, days=400)
        assert search(index, "clone timing")[0].score == pytest.approx(fresh)

    def test_global_memory_is_evergreen_too(self, index):
        index.index_file("MEMORY.md", "# G\n\nclone timing note\n", source="global")
        chunk_id = index.all_chunks()[0].chunk_id
        fresh = search(index, "clone timing")[0].score
        _age(index, chunk_id, days=400)
        assert search(index, "clone timing")[0].score == pytest.approx(fresh)

    def test_decay_follows_the_half_life(self, index):
        """`decayed = base * e^(-ln2/half_life * age_days)`: at exactly one
        half-life the multiplier is 0.5."""
        from githi.memory.search import decay_factor

        assert decay_factor(age_days=30, half_life_days=30) == pytest.approx(0.5)
        assert decay_factor(age_days=0, half_life_days=30) == pytest.approx(1.0)
        assert decay_factor(age_days=60, half_life_days=30) == pytest.approx(0.25)

    def test_decay_never_reaches_zero(self, index):
        """Exponential decay asymptotes; a very old session must still be
        findable if nothing else matches."""
        from githi.memory.search import decay_factor

        assert decay_factor(age_days=10_000, half_life_days=30) > 0.0


class TestSourceWeighting:
    def test_curated_outranks_session_all_else_equal(self, index):
        index.index_file("MEMORY.md", "# M\n\nidentical body text here\n",
                         source="workspace")
        index.index_file("s.md", "# S\n\nidentical body text here\n", source="session")
        results = {r.path: r.score for r in search(index, "identical body text")}
        assert results["MEMORY.md"] > results["s.md"]

    def test_unknown_source_gets_a_neutral_weight(self, index):
        index.index_file("x.md", "# X\n\nsome text\n", source="mystery")
        assert search(index, "some text")


class TestAccessBoost:
    def test_frequently_accessed_chunk_ranks_higher(self, index):
        index.index_file("a.md", "# A\n\nshared token body\n", source="session")
        index.index_file("b.md", "# B\n\nshared token body\n", source="session")
        target = next(c.chunk_id for c in index.all_chunks() if c.path == "a.md")
        for _ in range(20):
            index.record_access([target])

        results = {r.path: r.score for r in search(index, "shared token")}
        assert results["a.md"] > results["b.md"]

    def test_zero_access_is_never_penalised(self, index):
        """Grok's boost floors at 1.0 -- a never-retrieved chunk must not be
        pushed down, or nothing new can ever surface."""
        from githi.memory.search import access_boost

        assert access_boost(0) == pytest.approx(1.0)
        assert access_boost(5) > 1.0

    def test_access_boost_is_sublinear(self, index):
        """ln1p growth: a chunk retrieved 1000 times must not permanently
        dominate everything else."""
        from githi.memory.search import access_boost

        assert access_boost(1000) < 2 * access_boost(30)


class TestMinScore:
    def test_weak_matches_are_filtered(self, index):
        """`min_score` cuts against the **normalised** score, so it is a
        relative filter within one result set, not an absolute quality bar.
        Scores are min-max mapped per query, so the weakest hit sits near 0
        and the strongest near 1 regardless of how good either really is.
        Demonstrated with two hits of clearly different strength."""
        index.index_file("strong.md", "# S\n\nclone timing clone timing clone timing\n",
                         source="session")
        index.index_file("weak.md", "# W\n\nclone mentioned once among much other "
                                    "unrelated filler text here\n", source="session")

        assert len(search(index, "clone timing")) == 2
        survivors = search(index, "clone timing", SearchConfig(min_score=0.5))
        assert [r.path for r in survivors] == ["strong.md"]

    def test_min_score_zero_keeps_everything(self, index):
        index.index_file("a.md", "# A\n\ntoken here\n", source="session")
        index.index_file("b.md", "# B\n\ntoken there\n", source="session")
        assert len(search(index, "token", SearchConfig(min_score=0.0))) == 2

    def test_recording_access_is_opt_in(self, index):
        """Searching must not mutate ranking state unless asked -- otherwise
        a diagnostic query permanently reshapes the corpus."""
        index.index_file("a.md", "# A\n\ntoken\n", source="workspace")
        search(index, "token")
        assert index.all_chunks()[0].access_count == 0

        search(index, "token", SearchConfig(record_access=True))
        assert index.all_chunks()[0].access_count == 1
