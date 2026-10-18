"""Scoring and ranking (port of Grok's `search.rs`).

Grok's pipeline, in order:

1. FTS5 keyword search (always available)
2. Vector KNN, when embeddings exist              -- later phase
3. Merge by chunk_id, normalise to [0,1]
4. Drop content-free chunks (scaffold stubs)
5. Temporal decay -- **session sources only**
6. Source weights + access boost, filter by min_score
7. MMR diversity re-rank                          -- later phase
8. Truncate to max_results

Stage 5 is where the real opinion lives. Curated memory (`global`,
`workspace`) is **evergreen**: a fact a human wrote into MEMORY.md should not
fade merely because it is old. Session logs decay, because a raw transcript
from four months ago is usually superseded. Decaying both equally would
slowly bury curated knowledge under whatever happened most recently, which
is the opposite of what a memory is for.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

from .index import IndexedChunk, MemoryIndex
from .storage import is_scaffold_template

DAY_SECONDS = 86_400

EVERGREEN_SOURCES = frozenset({"global", "workspace"})
"""Sources exempt from temporal decay -- curated, human-facing knowledge."""

DEFAULT_SOURCE_WEIGHTS = {
    "global": 1.10,
    "workspace": 1.05,
    "session": 0.90,
}
NEUTRAL_WEIGHT = 1.0


@dataclass
class SearchConfig:
    max_results: int = 8
    min_score: float = 0.0
    half_life_days: float = 30.0
    source_weights: dict[str, float] = field(
        default_factory=lambda: dict(DEFAULT_SOURCE_WEIGHTS))
    candidate_limit: int = 50
    record_access: bool = False
    """Off by default: a search should not reshape ranking state as a side
    effect, or a diagnostic query permanently reorders the corpus."""

    query_embedding: list[float] | None = None
    """When present, KNN runs and its score merges with BM25. When absent,
    this is Grok's documented FTS-only path."""

    text_weight: float = 0.6
    vector_weight: float = 0.4
    mmr: object | None = None
    """An `MmrConfig`, or None to skip diversity re-ranking. Typed loosely to
    keep `search` from importing `mmr`, which imports `SearchResult` from
    here."""


@dataclass
class SearchResult:
    chunk_id: str
    path: str
    source: str
    score: float
    snippet: str
    start_line: int
    end_line: int
    created_at: int


def decay_factor(age_days: float, half_life_days: float) -> float:
    """`e^(-ln2/half_life * age_days)` -- 1.0 fresh, 0.5 at one half-life.

    Exponential rather than linear so it asymptotes instead of hitting zero:
    a very old session stays findable when nothing else matches, which is
    what you want from a memory that is allowed to be wrong but not absent.
    """
    if half_life_days <= 0 or age_days <= 0:
        return 1.0
    return math.exp(-math.log(2.0) / half_life_days * age_days)


def access_boost(access_count: int) -> float:
    """`1 + ln(1 + n) * 0.05`, exactly Grok's shape.

    Two properties, both deliberate. The floor is 1.0, so a never-retrieved
    chunk is *never penalised* -- otherwise nothing new could surface past
    established entries. And growth is sub-linear, so a chunk retrieved a
    thousand times cannot permanently dominate.

    Note this is a *popularity* signal, not a success signal: it records that
    a chunk was returned, never that returning it helped.
    """
    return 1.0 + math.log1p(max(0, access_count)) * 0.05


def is_content_free(text: str, source: str) -> bool:
    """Empty or boilerplate chunks that should never reach the model.

    The scaffold check is scoped to evergreen sources because that is where
    stubs live. A *session* chunk that quotes the stub's wording is real
    content -- someone talking about the template is not the template.
    """
    if _is_structurally_empty(text):
        return True
    return source in EVERGREEN_SOURCES and is_scaffold_template(text)


def _is_structurally_empty(text: str) -> bool:
    """True when nothing survives stripping markdown headers and punctuation."""
    body = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        body.append(stripped)
    return not "".join(body).strip(" #*-_`>|.")


def search(index: MemoryIndex, query: str,
           config: SearchConfig | None = None) -> list[SearchResult]:
    config = config or SearchConfig()
    fts_hits = index.search_fts(query, limit=config.candidate_limit)
    knn_hits = (index.search_knn(config.query_embedding, limit=config.candidate_limit)
                if config.query_embedding else [])
    if not fts_hits and not knn_hits:
        return []

    scored = _merge(fts_hits, knn_hits, config)
    now = time.time()
    results: list[SearchResult] = []
    relevance: list[float] = []

    for chunk, base in scored:
        if is_content_free(chunk.text, chunk.source):
            continue

        score = base
        if chunk.source not in EVERGREEN_SOURCES:
            age_days = max(0.0, (now - chunk.created_at) / DAY_SECONDS)
            score *= decay_factor(age_days, config.half_life_days)

        score *= config.source_weights.get(chunk.source, NEUTRAL_WEIGHT)
        score *= access_boost(chunk.access_count)

        if score < config.min_score:
            continue

        relevance.append(score)
        results.append(SearchResult(
            chunk_id=chunk.chunk_id, path=chunk.path, source=chunk.source,
            # Rank on the unclamped score, display it clamped: weights and
            # boosts can push past 1.0, and a ">1" score is meaningless to a
            # reader while the ordering it produced is not.
            score=min(1.0, score), snippet=chunk.text,
            start_line=chunk.start_line, end_line=chunk.end_line,
            created_at=chunk.created_at,
        ))

    order = sorted(range(len(results)), key=lambda i: relevance[i], reverse=True)
    results = [results[i] for i in order]
    relevance = [relevance[i] for i in order]

    if config.mmr is not None and len(results) > 1:
        from .mmr import mmr_rerank

        # The unclamped relevance is passed deliberately -- see mmr_rerank.
        mmr_rerank(results, relevance, config.mmr)

    results = results[: config.max_results]
    if config.record_access and results:
        index.record_access([r.chunk_id for r in results])
    return results


def _merge(fts: list[IndexedChunk], knn: list[IndexedChunk],
           config: SearchConfig) -> list[tuple[IndexedChunk, float]]:
    """Combine BM25 and KNN scores, keyed by chunk id.

    The weighting rule is Grok's, and the asymmetry is deliberate:

    * both signals   → ``text_weight * fts + vector_weight * vec``
    * FTS only       → **the full normalised fts score, unscaled**
    * vector only    → ``vector_weight * vec``

    Scaling an FTS-only chunk down to ``text_weight`` would penalise it for
    something it has no control over — whether *other* chunks happened to
    have embeddings — so a perfect keyword match would rank below a mediocre
    hybrid one purely because the corpus was partially embedded.
    """
    fts_scores = _normalise([c.score for c in fts])
    knn_scores = {c.chunk_id: c.score for c in knn}

    by_id: dict[str, IndexedChunk] = {c.chunk_id: c for c in knn}
    by_id.update({c.chunk_id: c for c in fts})       # prefer the FTS row

    merged: list[tuple[IndexedChunk, float]] = []
    seen: set[str] = set()

    for chunk, text_score in zip(fts, fts_scores, strict=True):
        seen.add(chunk.chunk_id)
        vector_score = knn_scores.get(chunk.chunk_id)
        if vector_score is None:
            merged.append((chunk, text_score))
        else:
            merged.append((chunk, config.text_weight * text_score
                           + config.vector_weight * vector_score))

    for chunk in knn:
        if chunk.chunk_id not in seen:
            merged.append((by_id[chunk.chunk_id], config.vector_weight * chunk.score))

    return merged


def _normalise(scores: list[float]) -> list[float]:
    """Map raw BM25 ranks onto [0,1] so scores are comparable across queries.

    The degenerate case matters: with a single hit (or identical scores)
    min == max, and textbook min-max normalisation divides by zero or scores
    the only answer at 0.0 — silently reporting the correct result as
    worthless. Those collapse to 1.0 instead.
    """
    if not scores:
        return []
    low, high = min(scores), max(scores)
    span = high - low
    if span <= 0:
        return [1.0] * len(scores)
    return [(s - low) / span for s in scores]
