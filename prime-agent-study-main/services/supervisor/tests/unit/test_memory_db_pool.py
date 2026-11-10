"""The memory service's Postgres connections per replica (``memory_service.db.pool_settings``):
SQLAlchemy's defaults until set, and a bad setting fails the replica's start."""

from __future__ import annotations

import pytest

from supervisor.memory_service.db import make_engine, pool_settings


def test_unset_is_sqlalchemys_default() -> None:
    assert pool_settings({}) == {"pool_size": 5, "max_overflow": 10}


def test_set_sizes_the_pool() -> None:
    assert pool_settings({"MEMORY_DB_POOL_SIZE": "2", "MEMORY_DB_MAX_OVERFLOW": "0"}) == {"pool_size": 2, "max_overflow": 0}


@pytest.mark.parametrize("value", ["two", "-1", "0"])  # 0 would be an unbounded pool
def test_a_bad_setting_fails_at_once(value: str) -> None:
    with pytest.raises(ValueError, match="MEMORY_DB_POOL_SIZE|invalid literal"):
        pool_settings({"MEMORY_DB_POOL_SIZE": value})


def test_a_postgres_engine_takes_the_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEMORY_DB_POOL_SIZE", "3")
    monkeypatch.setenv("MEMORY_DB_MAX_OVERFLOW", "1")
    engine = make_engine("postgresql+asyncpg://u:p@127.0.0.1:1/memory")  # not connected: an engine is lazy
    assert (engine.pool.size(), engine.pool._max_overflow) == (3, 1)  # type: ignore[attr-defined]


def test_a_setting_that_is_not_a_number_names_itself() -> None:
    import pytest

    with pytest.raises(ValueError, match="MEMORY_DB_POOL_SIZE must be a whole number, not 'ten'"):
        pool_settings({"MEMORY_DB_POOL_SIZE": "ten"})
