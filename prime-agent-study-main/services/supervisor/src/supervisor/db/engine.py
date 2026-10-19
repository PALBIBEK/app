"""Async database engine with production-ready connection pooling.

Configuration via environment variables:
    DATABASE_URL: Connection string (default: sqlite+aiosqlite:///:memory:)
    DB_POOL_SIZE: Base pool size (default: 20 for postgres, 5 for sqlite)
    DB_MAX_OVERFLOW: Extra connections under load (default: 10)
    DB_POOL_RECYCLE: Seconds before recycling stale connections (default: 3600)
    DB_POOL_PRE_PING: Check connection health before use (default: true)
    DB_ECHO: Log SQL statements (default: false)
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from supervisor.db.models import Base


def _env_bool(name: str, default: bool = False) -> bool:
    """Parse boolean from environment variable."""
    val = os.environ.get(name, "").strip().lower()
    if not val:
        return default
    return val in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    """Parse integer from environment variable."""
    val = os.environ.get(name, "").strip()
    if not val:
        return default
    try:
        return int(val)
    except ValueError:
        return default


def get_database_url() -> str:
    """Get database URL from environment or default to in-memory SQLite."""
    return os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///:memory:").strip()


def make_engine(url: str | None = None) -> AsyncEngine:
    """Create async engine with production-ready pool settings.

    Pool settings are tuned for 100+ concurrent users:
    - pool_size: Base connections kept open (20 for postgres)
    - max_overflow: Extra connections under load (10)
    - pool_recycle: Recycle stale connections after 1 hour
    - pool_pre_ping: Check connection health before use

    Args:
        url: Database URL. If None, reads from DATABASE_URL env var.

    Returns:
        Configured AsyncEngine instance.
    """
    resolved_url = url or get_database_url()

    kwargs: dict[str, Any] = {
        "echo": _env_bool("DB_ECHO", False),
    }

    if resolved_url.startswith("postgresql"):
        # Production settings for PostgreSQL
        kwargs.update({
            "pool_size": _env_int("DB_POOL_SIZE", 20),
            "max_overflow": _env_int("DB_MAX_OVERFLOW", 10),
            "pool_recycle": _env_int("DB_POOL_RECYCLE", 3600),
            "pool_pre_ping": _env_bool("DB_POOL_PRE_PING", True),
        })
    elif resolved_url.startswith("sqlite"):
        # SQLite: smaller pool, no pre-ping needed. Writers wait for the lock
        # (sqlite3's own busy timeout) instead of failing at once.
        kwargs.update({
            "pool_size": _env_int("DB_POOL_SIZE", 5),
            "max_overflow": _env_int("DB_MAX_OVERFLOW", 0),
            "connect_args": {"timeout": _SQLITE_BUSY_TIMEOUT_S},
        })

    engine = create_async_engine(resolved_url, **kwargs)
    if resolved_url.startswith("sqlite") and ":memory:" not in resolved_url:
        event.listen(engine.sync_engine, "connect", _sqlite_concurrent_writers)
    return engine


_SQLITE_BUSY_TIMEOUT_S = 30


def _sqlite_concurrent_writers(dbapi_connection: Any, _record: Any) -> None:
    """WAL lets readers run beside the writer; the busy timeout queues writers.

    Without both, two coroutines writing at once (a turn's journal and its
    post-turn bookkeeping) fail with "database is locked".
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute(f"PRAGMA busy_timeout={_SQLITE_BUSY_TIMEOUT_S * 1000}")
    cursor.close()


async def create_all(engine: AsyncEngine) -> None:
    """Create all tables defined in models.Base.metadata."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def drop_all(engine: AsyncEngine) -> None:
    """Drop all tables defined in models.Base.metadata (use with caution)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create session factory for the given engine.

    Sessions are configured with:
    - expire_on_commit=False: Objects remain usable after commit
    """
    return async_sessionmaker(engine, expire_on_commit=False)


async def check_health(engine: AsyncEngine) -> bool:
    """Check database connectivity.

    Returns:
        True if database is reachable, False otherwise.
    """
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


@asynccontextmanager
async def open_db(
    url: str | None = None,
) -> AsyncIterator[tuple[AsyncEngine, async_sessionmaker[AsyncSession]]]:
    """Context manager for database lifecycle.

    Creates engine, initializes tables, yields (engine, session_factory),
    and disposes engine on exit.

    Args:
        url: Database URL. If None, reads from DATABASE_URL env var.

    Yields:
        Tuple of (engine, session_factory).

    Example:
        async with open_db() as (engine, db):
            async with db() as session:
                result = await session.execute(...)
    """
    engine = make_engine(url)
    await create_all(engine)
    try:
        yield engine, session_factory(engine)
    finally:
        await engine.dispose()
