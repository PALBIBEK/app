"""Hybrid recall: relevance (vector + lexical) gated, then weighted with recency and confidence; token pack."""

from __future__ import annotations

import re
import time
from typing import Final

from supervisor.memory_service.bank import MemoryBank, _row_to_item
from supervisor.memory_service.embed import Embedder, HashingEmbedder, cosine
from supervisor.memory_service.models import MemoryItemRow
from supervisor.memory_service.tier2_types import (
    MemoryItem,
    RecallResult,
    TokenBudget,
    estimate_tokens,
)
from supervisor.memory_service.types import Scope

_TOKEN = re.compile(r"[a-z0-9_./:-]+", re.IGNORECASE)


def _tokens(text: str) -> set[str]:
    return set(_TOKEN.findall(text.lower()))


def lexical_score(query: str, content: str) -> float:
    qt = _tokens(query)
    ct = _tokens(content)
    if not qt or not ct:
        return 0.0
    overlap = len(qt & ct)
    # Prefer exact identifier hits
    exact = sum(1 for t in qt if t in content.lower())
    return overlap / len(qt) + 0.5 * (exact / max(1, len(qt)))


# Ranking, after the systems that do this in production (see the PR for the code read):
# - mem0 (mem0/memory/main.py, mem0/utils/scoring.py ``score_and_rank``): a candidate
#   whose semantic similarity is below ``threshold`` (default 0.1) is excluded before any
#   other signal can lift it; relevance is the semantic and keyword scores added and
#   divided by the maximum possible, so it lies in [0, 1].
# - Generative Agents (reverie/.../retrieve.py ``new_retrieve``): recency, relevance and
#   importance are each min-max normalized over the candidates to [0, 1] and combined with
#   weights [0.5, 3, 2]; importance here is the item's confidence.
# - LangChain ``TimeWeightedVectorStoreRetriever``: recency is (1 - decay_rate) ** hours
#   since the item, decay_rate 0.01.
MIN_SEMANTIC: Final = 0.1
RECENCY_DECAY: Final = 0.01
W_RECENCY, W_RELEVANCE, W_IMPORTANCE = 0.5, 3.0, 2.0
_LEXICAL_MAX: Final = 1.5  # lexical_score's maximum (all terms overlap and all are exact)


def _normalized(values: dict[str, float]) -> dict[str, float]:
    """Min-max to [0, 1]; all equal is 0.5 each (Generative Agents' normalize_dict_floats)."""
    low, high = min(values.values()), max(values.values())
    if high == low:
        return dict.fromkeys(values, 0.5)
    return {k: (v - low) / (high - low) for k, v in values.items()}


class HybridRecall:
    def __init__(
        self,
        bank: MemoryBank,
        embedder: Embedder | None = None,
    ) -> None:
        self._bank = bank
        self._embedder = embedder or HashingEmbedder()

    def rank(self, rows: list[MemoryItemRow], query: str, *, now_ms: int) -> list[tuple[MemoryItemRow, float]]:
        """Items related to ``query``, best first, with their scores.

        Unrelated items (semantic similarity below MIN_SEMANTIC) are not returned at all:
        a recall with nothing relevant is empty, not padded with whatever is newest."""
        q_emb = self._embedder.embed(query)
        relevance: dict[str, float] = {}
        related: dict[str, MemoryItemRow] = {}
        for row in rows:
            semantic = cosine(q_emb, list(row.embedding or []))
            if semantic < MIN_SEMANTIC:
                continue
            keyword = min(lexical_score(query, row.content) / _LEXICAL_MAX, 1.0)
            relevance[row.id] = (semantic + keyword) / 2.0
            related[row.id] = row
        if not related:
            return []
        recency = _normalized(
            {
                i: (1.0 - RECENCY_DECAY) ** (max(0, now_ms - r.valid_from_ms) / 3_600_000)
                for i, r in related.items()
            }
        )
        importance = _normalized({i: float(r.confidence) for i, r in related.items()})
        relevant = _normalized(relevance)
        scored = [
            (row, W_RECENCY * recency[i] + W_RELEVANCE * relevant[i] + W_IMPORTANCE * importance[i])
            for i, row in related.items()
        ]
        scored.sort(key=lambda pair: (-pair[1], -pair[0].valid_from_ms))
        return scored

    async def recall(
        self,
        scope: Scope,
        query: str,
        budget: TokenBudget,
    ) -> RecallResult:
        actives = await self._bank.list_active(scope)
        ranked = self.rank(actives, query, now_ms=int(time.time() * 1000))
        packed: list[MemoryItem] = []
        tokens = 0
        for row, _score in ranked:
            cost = estimate_tokens(row.content)
            if tokens + cost > budget.max_tokens:
                continue  # all or nothing per item; a smaller one further down may still fit
            packed.append(_row_to_item(row))
            tokens += cost
        ids = tuple(i.id for i in packed)
        return RecallResult(items=tuple(packed), item_ids=ids, tokens_used=tokens)
