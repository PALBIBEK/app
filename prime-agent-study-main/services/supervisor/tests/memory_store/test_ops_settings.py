"""Operational bounds: the embedding cache has a size, and a deployed memory service does
not fall back to a SQLite file in its working directory."""

from __future__ import annotations

import pytest

from supervisor.memory_service.cost import CachingEmbedder
from supervisor.memory_service.db import database_url_from_env


def test_the_embedding_cache_keeps_the_most_recently_used() -> None:
    calls: list[str] = []

    class Counting:
        def embed(self, text: str) -> list[float]:
            calls.append(text)
            return [float(len(text))]

    cache = CachingEmbedder(Counting(), size=3)
    for text in ("a", "b", "c", "a", "d"):  # "a" is used again, so "b" is the oldest
        cache.embed(text)
    assert calls == ["a", "b", "c", "d"]
    for text in ("a", "c", "d"):
        cache.embed(text)
    assert calls == ["a", "b", "c", "d"], "all three still cached"
    cache.embed("b")
    assert calls[-1] == "b", "b was evicted"
    assert len(cache._cache) == 3


@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        ({}, "sqlite+aiosqlite:///./memory.db"),
        ({"ENV": "development"}, "sqlite+aiosqlite:///./memory.db"),
        ({"ENV": "staging", "MEMORY_DATABASE_URL": "sqlite+aiosqlite:////data/memory.db"}, "sqlite+aiosqlite:////data/memory.db"),
        ({"ENV": "production", "MEMORY_DATABASE_URL": "postgresql+asyncpg://h/memory"}, "postgresql+asyncpg://h/memory"),
        (
            {"ENVIRONMENT": "production", "MEMORY_DATABASE_URL": "sqlite+aiosqlite:////d/m.db", "MEMORY_ALLOW_SQLITE": "1"},
            "sqlite+aiosqlite:////d/m.db",
        ),
    ],
)
def test_the_database_url(environ: dict[str, str], expected: str) -> None:
    assert database_url_from_env(environ) == expected


@pytest.mark.parametrize(
    "environ",
    [
        {"ENV": "production"},
        {"ENV": "staging", "MEMORY_DATABASE_URL": "  "},
        {"ENV": "production", "MEMORY_DATABASE_URL": "sqlite+aiosqlite:////d/m.db"},
    ],
)
def test_a_deployed_service_refuses_a_missing_or_sqlite_url(environ: dict[str, str]) -> None:
    with pytest.raises(RuntimeError, match="MEMORY_DATABASE_URL"):
        database_url_from_env(environ)
