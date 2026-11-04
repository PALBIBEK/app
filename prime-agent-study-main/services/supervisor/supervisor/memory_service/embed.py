"""Pluggable embeddings — HashingEmbedder for CI (no vendor keys)."""

from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol, runtime_checkable

from supervisor.memory_service.text import STOPWORDS

_TOKEN = re.compile(r"[a-z0-9_./:-]+", re.IGNORECASE)

EMBED_DIM = 1024
"""Buckets of the hashing trick. scikit-learn's HashingVectorizer (default 2**20 features)
notes that "small numbers of features are likely to cause hash collisions"; at 64 two
unrelated texts often shared buckets, so their similarity was noise above any sensible
relevance floor. Stored vectors of another size are re-embedded by schema migration 5."""


@runtime_checkable
class Embedder(Protocol):
    def embed(self, text: str) -> list[float]: ...


class HashingEmbedder:
    """Deterministic bag-of-tokens hashing trick → unit vector."""

    def __init__(self, dim: int = EMBED_DIM) -> None:
        self.dim = dim

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        tokens = [t for t in _TOKEN.findall(text.lower()) if t not in STOPWORDS]
        if not tokens:
            return vec
        for tok in tokens:
            h = int(hashlib.sha256(tok.encode()).hexdigest(), 16)
            idx = h % self.dim
            sign = 1.0 if (h >> 8) & 1 else -1.0
            vec[idx] += sign
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    return float(sum(x * y for x, y in zip(a, b, strict=True)))


def content_hash(text: str) -> str:
    return hashlib.sha256(text.strip().encode()).hexdigest()
