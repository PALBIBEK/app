"""Hybrid recall — vector + lexical + recency + confidence → RRF → token pack."""

from __future__ import annotations

import math
import re
import time

from memory_service.bank import MemoryBank, _row_to_item
from memory_service.embed import Embedder, HashingEmbedder, cosine
from memory_service.tier2_types import MemoryItem, RecallResult, TokenBudget, estimate_tokens
from memory_service.types import Scope

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


def rrf(rank: int, k: int = 60) -> float:
    return 1.0 / (k + rank)


class HybridRecall:
    def __init__(
        self,
        bank: MemoryBank,
        embedder: Embedder | None = None,
    ) -> None:
        self._bank = bank
        self._embedder = embedder or HashingEmbedder()

    async def recall(
        self,
        scope: Scope,
        query: str,
        budget: TokenBudget,
    ) -> RecallResult:
        actives = await self._bank.list_active(scope)
        if not actives:
            return RecallResult(items=(), item_ids=(), tokens_used=0)

        q_emb = self._embedder.embed(query)
        now = int(time.time() * 1000)

        # Rank lists
        by_vec = sorted(
            actives,
            key=lambda r: cosine(q_emb, list(r.embedding or [])),
            reverse=True,
        )
        by_lex = sorted(
            actives,
            key=lambda r: lexical_score(query, r.content),
            reverse=True,
        )
        by_recency = sorted(actives, key=lambda r: r.valid_from_ms, reverse=True)
        by_conf = sorted(actives, key=lambda r: float(r.confidence), reverse=True)

        scores: dict[str, float] = {r.id: 0.0 for r in actives}
        for rank, row in enumerate(by_vec, start=1):
            scores[row.id] += rrf(rank)
        for rank, row in enumerate(by_lex, start=1):
            scores[row.id] += rrf(rank)
        for rank, row in enumerate(by_recency, start=1):
            scores[row.id] += 0.5 * rrf(rank)
        for rank, row in enumerate(by_conf, start=1):
            scores[row.id] += 0.5 * rrf(rank)

        # Soft recency boost for very new items
        for row in actives:
            age_h = max(0.0, (now - row.valid_from_ms) / 3_600_000)
            scores[row.id] += 0.05 * math.exp(-age_h / 24.0)

        ranked = sorted(actives, key=lambda r: scores[r.id], reverse=True)
        packed: list[MemoryItem] = []
        tokens = 0
        for row in ranked:
            cost = estimate_tokens(row.content)
            if tokens + cost > budget.max_tokens and packed:
                break
            if tokens + cost > budget.max_tokens and not packed:
                # Always allow at least one if it alone overflows? skip if huge
                if cost > budget.max_tokens:
                    continue
            packed.append(_row_to_item(row))
            tokens += cost
            if tokens >= budget.max_tokens:
                break

        ids = tuple(i.id for i in packed)
        return RecallResult(items=tuple(packed), item_ids=ids, tokens_used=tokens)
