"""Embedding providers (port of Grok's `embedding.rs`).

Three implementations, each with a job:

* ``ApiEmbeddingProvider``     — a real OpenAI-compatible ``/embeddings`` endpoint
* ``HashingEmbeddingProvider`` — deterministic local vectors, no network
* ``NullEmbeddingProvider``    — the explicit "no embeddings" path

The null provider exists so callers never need a `None` check: "embeddings
are unavailable" is a provider that returns nothing, not a missing object.
That is what makes FTS-only degradation a normal path rather than an error
branch that only runs when something is already broken.
"""

from __future__ import annotations

import hashlib
import math
import os
from typing import Protocol, runtime_checkable

DEFAULT_BATCH_SIZE = 32
DEFAULT_DIMENSIONS = 256


@runtime_checkable
class EmbeddingProvider(Protocol):
    @property
    def dimensions(self) -> int: ...

    def embed_batch(self, texts: list[str]) -> list[list[float]]: ...


def _normalise(vector: list[float]) -> list[float]:
    """Scale to unit length.

    Cosine similarity collapses to a plain dot product only for unit
    vectors, which is what `vec0` computes. Skipping this makes magnitude
    leak into the score, so longer chunks rank higher for being longer.
    """
    norm = math.sqrt(sum(x * x for x in vector))
    if norm == 0:
        return vector
    return [x / norm for x in vector]


class HashingEmbeddingProvider:
    """Deterministic vectors from a hash of the text.

    Not semantic — it cannot place "clone" near "repository". It exists so
    the storage, KNN and merge machinery can be exercised offline, in tests
    and on air-gapped machines, without a key or a network round trip.
    """

    def __init__(self, dimensions: int = DEFAULT_DIMENSIONS) -> None:
        self._dimensions = dimensions
        self.max_batch_size = DEFAULT_BATCH_SIZE

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed_one(self, text: str) -> list[float]:
        digest = hashlib.blake2b(text.encode("utf-8"),
                                 digest_size=max(1, self._dimensions)).digest()
        raw = [(b / 255.0) - 0.5 for b in digest[: self._dimensions]]
        raw.extend([0.0] * (self._dimensions - len(raw)))
        return _normalise(raw)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_one(t) for t in texts]


class NullEmbeddingProvider:
    """No embeddings. Search degrades to FTS-only with `text_weight = 1.0`."""

    max_batch_size = DEFAULT_BATCH_SIZE

    @property
    def dimensions(self) -> int:
        return 0

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return []


class ApiEmbeddingProvider:
    """An OpenAI-compatible `/embeddings` endpoint (OpenRouter, OpenAI, ...)."""

    def __init__(self, api_key: str = "", model: str = "openai/text-embedding-3-small",
                 base_url: str = "https://openrouter.ai/api/v1",
                 dimensions: int = 1536, http_client=None,
                 max_batch_size: int = DEFAULT_BATCH_SIZE) -> None:
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._dimensions = dimensions
        self.max_batch_size = max_batch_size
        self._client = http_client

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        import httpx

        client = self._client or httpx.Client(timeout=60.0)
        response = client.post(
            f"{self.base_url}/embeddings",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model, "input": texts},
        )
        response.raise_for_status()
        data = response.json()["data"]
        # Providers do not guarantee response order matches input order; the
        # index field is authoritative and mis-ordering would silently attach
        # every vector to the wrong chunk.
        ordered = sorted(data, key=lambda d: d.get("index", 0))
        return [_normalise(item["embedding"]) for item in ordered]


def embed_missing_chunks(index, provider: EmbeddingProvider) -> int:
    """Embed only chunks that lack a vector, in provider-sized batches.

    Called after any write. The "only missing" part is the cost property:
    without it, saving a one-line edit re-embeds the entire corpus and turns
    a trivial change into a paid pass over everything.

    A provider failure is logged-and-skipped rather than raised — an
    embedding endpoint being down should cost semantic search, not the run.
    """
    if getattr(provider, "dimensions", 0) <= 0:
        return 0

    pending = index.chunks_without_embeddings()
    if not pending:
        return 0

    batch_size = getattr(provider, "max_batch_size", DEFAULT_BATCH_SIZE)
    embedded = 0

    for start in range(0, len(pending), batch_size):
        batch = pending[start: start + batch_size]
        try:
            vectors = provider.embed_batch([text for _, text in batch])
        except Exception:  # noqa: BLE001 - degrade to FTS-only, never fail the run
            continue
        for (chunk_id, _), vector in zip(batch, vectors, strict=False):
            if index.upsert_embedding(chunk_id, vector):
                embedded += 1
    return embedded
