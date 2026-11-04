"""Cost controls — embed cache by content hash; barren-turn detection."""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

from supervisor.memory_service.embed import Embedder, HashingEmbedder, content_hash
from supervisor.memory_service.metrics import METRICS
from supervisor.memory_service.types import SessionEvent

EMBED_CACHE_SIZE = 20_000
"""Vectors kept per process; the least recently used goes first."""


class CachingEmbedder:
    """Wraps an Embedder; cache by content hash (Phase 5 cost control), bounded (LRU)."""

    def __init__(self, inner: Embedder | None = None, *, size: int = EMBED_CACHE_SIZE) -> None:
        self._inner = inner or HashingEmbedder()
        self._cache: OrderedDict[str, list[float]] = OrderedDict()
        self._size = size

    def embed(self, text: str) -> list[float]:
        key = content_hash(text)
        if (vec := self._cache.get(key)) is not None:
            self._cache.move_to_end(key)
            METRICS.inc("embed_cache_hits")
            return vec
        METRICS.inc("embed_cache_misses")
        vec = self._inner.embed(text)
        self._cache[key] = vec
        if len(self._cache) > self._size:
            self._cache.popitem(last=False)
        return vec



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
