"""Maximal Marginal Relevance diversity re-ranking (port of Grok's `mmr.rs`).

Without it, a user with several memory chunks on one topic gets top results
that are near-identical: five ways of saying the same thing, occupying every
slot the model will read. MMR greedily picks results that trade relevance
against redundancy:

    MMR(d) = λ · relevance(d) − (1 − λ) · max_similarity(d, already_selected)

Similarity is **Jaccard over tokens**, not embeddings. That is a deliberate
economy: diversity only needs a rough "are these two saying the same thing",
and computing it lexically avoids a second embedding round trip per query.
O(n²), but n is the 6–18 candidates left after scoring.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .search import SearchResult

_TOKEN = re.compile(r"[A-Za-z0-9_]+")


@dataclass
class MmrConfig:
    enabled: bool = True
    lambda_: float = 0.7
    """1.0 = pure relevance (no-op), 0.0 = pure diversity. Grok's default
    leans toward relevance: diversity is a tiebreak, not the objective."""


def tokenize(text: str) -> set[str]:
    """Split into a token set for Jaccard.

    Expects **pre-lowered** input — callers lowercase once so `Rust` and
    `rust` collapse, since casing varies freely across markdown sources.
    Unlike query expansion this keeps stop words: for measuring *overlap*
    between two snippets, shared common words are real evidence of
    redundancy.
    """
    return set(_TOKEN.findall(text))


def jaccard_similarity(a: set[str], b: set[str]) -> float:
    """|A ∩ B| / |A ∪ B|.

    Two empty sets count as identical (both say nothing, so keeping both is
    pointless); one empty counts as disjoint (nothing to compare).
    """
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    intersection = len(a & b)
    union = len(a) + len(b) - intersection
    return intersection / union if union else 0.0


def mmr_rerank(results: list[SearchResult], relevance: list[float],
               config: MmrConfig | None = None) -> None:
    """Reorder `results` in place. Never adds or drops anything.

    `relevance` is passed separately rather than read from
    `SearchResult.score` because that field is **clamped to 1.0** for
    display. Several strong chunks saturate at exactly 1.0, which erases the
    ordering — including the access-frequency tiebreak — that MMR needs to
    trade against diversity.
    """
    config = config or MmrConfig()
    if not config.enabled or len(results) <= 1 or config.lambda_ >= 1.0:
        return
    if len(relevance) != len(results):
        raise ValueError("relevance must be aligned index-for-index with results")

    tokens = [tokenize(r.snippet.lower()) for r in results]

    low, high = min(relevance), max(relevance)
    span = high - low
    if span <= 0:
        span = 1.0                 # identical relevance: normalisation is a no-op

    selected: list[int] = []
    remaining = list(range(len(results)))

    while remaining:
        best_pos, best_score = 0, float("-inf")
        for position, candidate in enumerate(remaining):
            normalized = (relevance[candidate] - low) / span
            redundancy = max(
                (jaccard_similarity(tokens[candidate], tokens[s]) for s in selected),
                default=0.0,
            )
            score = config.lambda_ * normalized - (1.0 - config.lambda_) * redundancy
            if score > best_score:
                best_pos, best_score = position, score
        selected.append(remaining.pop(best_pos))

    results[:] = [results[i] for i in selected]
