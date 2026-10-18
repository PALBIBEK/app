"""Cost controls — embed cache by content hash; barren-turn detection."""

from __future__ import annotations

from typing import Any

from memory_service.embed import Embedder, HashingEmbedder, content_hash
from memory_service.metrics import METRICS
from memory_service.types import SessionEvent


class CachingEmbedder:
    """Wraps an Embedder; cache by content hash (Phase 5 cost control)."""

    def __init__(self, inner: Embedder | None = None) -> None:
        self._inner = inner or HashingEmbedder()
        self._cache: dict[str, list[float]] = {}

    def embed(self, text: str) -> list[float]:
        key = content_hash(text)
        if key in self._cache:
            METRICS.inc("embed_cache_hits")
            return self._cache[key]
        METRICS.inc("embed_cache_misses")
        vec = self._inner.embed(text)
        self._cache[key] = vec
        return vec

    def batch_embed(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]


def flush_batch_is_barren(events: list[SessionEvent] | list[Any]) -> bool:
    """
    True when there is nothing worth extracting (no user/assistant text).
    Most turns are barren — skip extraction enqueue entirely.
    """
    for ev in events:
        typ = ev.type if hasattr(ev, "type") else ev.get("type")  # type: ignore[union-attr]
        if typ not in ("user/message", "assistant/message"):
            continue
        data = ev.data if hasattr(ev, "data") else ev.get("data")  # type: ignore[union-attr]
        if not isinstance(data, dict):
            continue
        text = str(data.get("text") or data.get("content") or "").strip()
        if len(text) >= 8:
            return False
    return True
