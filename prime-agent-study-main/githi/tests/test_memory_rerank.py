"""MMR diversity, query expansion, and text classification.

Ports of Grok's `mmr.rs`, `query_expansion.rs` and `text_utils.rs`.

The unifying problem: raw retrieval is *too literal*. BM25 returns five
near-identical chunks about the same topic (MMR fixes that), dilutes
precision across articles and pronouns in conversational queries (query
expansion fixes that), and the consolidation pass needs to recognise when a
model declined to answer (text_utils fixes that).
"""

from __future__ import annotations

import pytest

from githi.memory.mmr import MmrConfig, jaccard_similarity, mmr_rerank, tokenize
from githi.memory.query_expansion import expand_query
from githi.memory.search import SearchResult
from githi.memory.text_utils import has_markdown_headers, is_no_reply


def _result(path: str, snippet: str, score: float = 1.0) -> SearchResult:
    return SearchResult(chunk_id=path, path=path, source="session", score=score,
                        snippet=snippet, start_line=1, end_line=2, created_at=0)


class TestTokenize:
    def test_splits_on_non_alphanumeric_keeping_underscore(self):
        """Case is preserved -- `tokenize` expects pre-lowered input (see
        below), so the caller lowercases once rather than paying for it per
        token."""
        assert tokenize("git log -S foo_bar") == {"git", "log", "S", "foo_bar"}

    def test_drops_empties(self):
        assert "" not in tokenize("a -- b")

    def test_expects_prelowered_input(self):
        """Grok lowercases once at the call site and tokenizes the result, so
        'Rust' and 'rust' collapse -- casing varies across markdown."""
        assert tokenize("Rust") != tokenize("rust")


class TestJaccard:
    def test_identical_sets(self):
        assert jaccard_similarity({"a", "b"}, {"a", "b"}) == pytest.approx(1.0)

    def test_disjoint_sets(self):
        assert jaccard_similarity({"a"}, {"b"}) == pytest.approx(0.0)

    def test_partial_overlap(self):
        assert jaccard_similarity({"a", "b"}, {"b", "c"}) == pytest.approx(1 / 3)

    def test_both_empty_is_identical(self):
        assert jaccard_similarity(set(), set()) == pytest.approx(1.0)

    def test_one_empty_is_disjoint(self):
        assert jaccard_similarity({"a"}, set()) == pytest.approx(0.0)


class TestMmr:
    def test_disabled_is_a_no_op(self):
        results = [_result("a", "x"), _result("b", "y")]
        before = list(results)
        mmr_rerank(results, [1.0, 0.9], MmrConfig(enabled=False))
        assert results == before

    def test_lambda_one_is_a_no_op(self):
        """lambda=1 means pure relevance, so reordering would be wrong."""
        results = [_result("a", "same text"), _result("b", "same text")]
        before = list(results)
        mmr_rerank(results, [1.0, 0.9], MmrConfig(lambda_=1.0))
        assert results == before

    def test_single_result_is_a_no_op(self):
        results = [_result("a", "x")]
        mmr_rerank(results, [1.0], MmrConfig())
        assert len(results) == 1

    def test_most_relevant_is_still_selected_first(self):
        """MMR reorders the tail; the top hit is chosen on relevance alone
        because nothing has been selected yet to be redundant against."""
        results = [_result("a", "alpha topic"), _result("b", "beta topic")]
        mmr_rerank(results, [1.0, 0.2], MmrConfig(lambda_=0.5))
        assert results[0].path == "a"

    def test_redundant_result_is_demoted(self):
        """The whole point: three hits where two are near-duplicates should
        not fill the top three slots with the same information."""
        results = [
            _result("a", "clone timing takes 190 seconds"),
            _result("b", "clone timing takes 190 seconds roughly"),
            _result("c", "git blame misattributes on shallow clones"),
        ]
        mmr_rerank(results, [1.0, 0.95, 0.90], MmrConfig(lambda_=0.5))
        assert results[0].path == "a"
        assert results[1].path == "c", "near-duplicate should not be second"

    def test_all_results_are_retained(self):
        """Re-ranking reorders; it must never drop anything -- truncation is
        `max_results`' job, and silently losing a hit here would be invisible."""
        results = [_result(c, f"text {c}") for c in "abcde"]
        mmr_rerank(results, [1.0, 0.9, 0.8, 0.7, 0.6], MmrConfig(lambda_=0.5))
        assert {r.path for r in results} == set("abcde")

    def test_uses_unclamped_relevance_not_the_display_score(self):
        """Grok passes relevance separately because `SearchResult.score` is
        clamped to 1.0 -- several top chunks saturate there, destroying the
        ordering MMR needs to trade against."""
        results = [_result("a", "alpha", score=1.0), _result("b", "beta", score=1.0)]
        mmr_rerank(results, [0.2, 5.0], MmrConfig(lambda_=0.9))
        assert results[0].path == "b", "ignored the unclamped relevance"

    def test_mismatched_relevance_length_raises(self):
        with pytest.raises(ValueError):
            mmr_rerank([_result("a", "x"), _result("b", "y")], [1.0], MmrConfig())

    def test_identical_relevance_does_not_crash(self):
        """Zero range -> division by zero in naive normalisation."""
        results = [_result("a", "same"), _result("b", "same")]
        mmr_rerank(results, [1.0, 1.0], MmrConfig(lambda_=0.5))
        assert len(results) == 2


class TestQueryExpansion:
    def test_removes_stop_words(self):
        assert expand_query("that thing we discussed about the API") == ["thing", "discussed", "api"]

    def test_lowercases(self):
        assert expand_query("The API Limits") == ["api", "limits"]

    def test_dedupes_preserving_order(self):
        assert expand_query("clone the clone timing") == ["clone", "timing"]

    def test_all_stop_words_returns_empty(self):
        """Grok's contract: an empty list signals the caller to fall back to
        the vector path rather than issue a meaningless FTS query."""
        assert expand_query("what is that?") == []

    def test_empty_query(self):
        assert expand_query("") == []

    def test_splits_on_punctuation_keeping_underscore(self):
        assert expand_query("git log -S foo_bar") == ["git", "log", "s", "foo_bar"]


class TestTextUtils:
    @pytest.mark.parametrize("text", ["NO_REPLY", "no reply", "No-Reply", "noreply",
                                      "  NO REPLY  "])
    def test_no_reply_variants(self, text):
        assert is_no_reply(text)

    @pytest.mark.parametrize("text", ["no reply needed", "I have things to store", ""])
    def test_not_no_reply(self, text):
        assert not is_no_reply(text)

    @pytest.mark.parametrize("text", ["## Topic", "# Title\n\nBody", "preamble\n\n## Topic"])
    def test_has_headers(self, text):
        assert has_markdown_headers(text)

    @pytest.mark.parametrize("text", ["plain text without headers", "#hashtag without space", ""])
    def test_no_headers(self, text):
        assert not has_markdown_headers(text)
