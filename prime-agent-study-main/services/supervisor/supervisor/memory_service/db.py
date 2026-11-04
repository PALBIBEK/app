"""Database engine and session factory."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

_DEV_DATABASE_URL = "sqlite+aiosqlite:///./memory.db"


def database_url_from_env(environ: Mapping[str, str] = os.environ) -> str:
    """``MEMORY_DATABASE_URL``, with a SQLite file in the working directory for local use.

    Deployed (``ENV``/``ENVIRONMENT`` production or staging) the URL must be set: the
    fallback lives in one container and is lost on redeploy. In production it must not
    be SQLite either (one writer, one replica) unless ``MEMORY_ALLOW_SQLITE`` says so."""
    url = environ.get("MEMORY_DATABASE_URL", "").strip()
    env = environ.get("ENV", environ.get("ENVIRONMENT", "")).strip().lower()
    if not url:
        if env in ("production", "staging"):
            raise RuntimeError(f"MEMORY_DATABASE_URL must be set when ENV={env} (long-term memory needs a database)")
        return _DEV_DATABASE_URL
    allowed = environ.get("MEMORY_ALLOW_SQLITE", "").lower() in ("1", "true", "yes")
    if env == "production" and url.startswith("sqlite") and not allowed:
        raise RuntimeError(
            "MEMORY_DATABASE_URL is SQLite with ENV=production: use Postgres "
            "(or set MEMORY_ALLOW_SQLITE=1 for a single-replica deployment)"
        )
    return url


def make_engine(database_url: str = "sqlite+aiosqlite:///:memory:") -> AsyncEngine:
    kwargs: dict = {"echo": False}
    if database_url.startswith("sqlite") and ":memory:" in database_url:
        kwargs["connect_args"] = {"check_same_thread": False}
        kwargs["poolclass"] = StaticPool
        database_url = "sqlite+aiosqlite://"
    return create_async_engine(database_url, **kwargs)


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def init_db(engine: AsyncEngine) -> None:
    """Create, migrate and check the schema (``supervisor.memory_service.schema``)."""
    from supervisor.memory_service.schema import migrate

    await migrate(engine)


@asynccontextmanager
async def session_scope(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
