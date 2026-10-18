"""
Connection pool configuration for production database access.

Provides configurable connection pooling for SQLAlchemy async engines
with production-ready defaults for health checks, timeouts, and sizing.

Pool Sizing Guidelines:
- min_size: Connections kept warm for instant availability
- max_size: Maximum concurrent connections (per worker process)
- max_overflow: Additional temporary connections under load

For 1000 concurrent users with 4 workers:
    connections_per_worker = 1000 / 4 / connections_per_request
    Typically 20-50 per worker is sufficient with async

Usage:
    from shared.db import create_async_engine, PoolConfig

    # Production settings
    engine = create_async_engine(
        "postgresql+asyncpg://user:pass@host/db",
        PoolConfig(min_size=10, max_size=30),
    )

    # With custom settings
    engine = create_async_engine(
        url,
        PoolConfig(
            min_size=5,
            max_size=20,
            max_overflow=10,
            pool_timeout=30.0,
            pool_recycle=3600,
            pool_pre_ping=True,
            echo_pool=True,  # Debug pool activity
        ),
    )
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

logger = logging.getLogger(__name__)

# Conditional imports for optional dependencies
try:
    from sqlalchemy.ext.asyncio import (
        AsyncEngine,
        AsyncSession,
        async_sessionmaker,
        create_async_engine as _create_engine,
    )
    from sqlalchemy.pool import QueuePool, NullPool
    from sqlalchemy import text

    HAS_SQLALCHEMY = True
except ImportError:
    HAS_SQLALCHEMY = False
    AsyncEngine = Any  # type: ignore
    AsyncSession = Any  # type: ignore


@dataclass(frozen=True)
class PoolConfig:
    """
    Connection pool configuration.

    Attributes:
        min_size: Minimum connections to keep in pool (pool_size in SQLAlchemy).
        max_size: Maximum pool size (min_size + max_overflow).
        max_overflow: Additional connections above min_size.
        pool_timeout: Seconds to wait for available connection.
        pool_recycle: Seconds before connection is recycled (prevents stale).
        pool_pre_ping: Enable connection health check on checkout.
        echo_pool: Log pool checkouts/checkins for debugging.
        connect_timeout: Seconds for initial connection establishment.
        command_timeout: Seconds for query execution timeout.
        statement_cache_size: Size of prepared statement cache (asyncpg).

    Example:
        ```python
        config = PoolConfig(
            min_size=10,
            max_size=50,
            pool_timeout=30.0,
            pool_pre_ping=True,
        )
        ```
    """

    min_size: int = 5
    max_size: int = 20
    max_overflow: int | None = None  # If None, computed as max_size - min_size
    pool_timeout: float = 30.0
    pool_recycle: int = 3600  # 1 hour
    pool_pre_ping: bool = True
    echo_pool: bool = False
    connect_timeout: float = 10.0
    command_timeout: float = 30.0
    statement_cache_size: int = 100

    def __post_init__(self) -> None:
        """Validate configuration."""
        if self.min_size < 1:
            raise ValueError("min_size must be >= 1")
        if self.max_size < self.min_size:
            raise ValueError("max_size must be >= min_size")
        if self.pool_timeout < 0:
            raise ValueError("pool_timeout must be >= 0")
        if self.pool_recycle < 0:
            raise ValueError("pool_recycle must be >= 0")

    @property
    def computed_overflow(self) -> int:
        """Get max_overflow, computing if not explicitly set."""
        if self.max_overflow is not None:
            return self.max_overflow
        return self.max_size - self.min_size

    def to_engine_kwargs(self) -> dict[str, Any]:
        """
        Convert to SQLAlchemy engine kwargs.

        Returns:
            Dict of kwargs for create_async_engine.
        """
        return {
            "poolclass": QueuePool,
            "pool_size": self.min_size,
            "max_overflow": self.computed_overflow,
            "pool_timeout": self.pool_timeout,
            "pool_recycle": self.pool_recycle,
            "pool_pre_ping": self.pool_pre_ping,
            "echo_pool": "debug" if self.echo_pool else False,
        }

    def to_connect_args(self) -> dict[str, Any]:
        """
        Convert to asyncpg connect args.

        Returns:
            Dict of kwargs for asyncpg connection.
        """
        return {
            "timeout": self.connect_timeout,
            "command_timeout": self.command_timeout,
            "statement_cache_size": self.statement_cache_size,
        }


def create_async_engine(
    database_url: str,
    config: PoolConfig | None = None,
    *,
    echo: bool = False,
    **extra_kwargs: Any,
) -> AsyncEngine:
    """
    Create an async SQLAlchemy engine with production pool settings.

    Args:
        database_url: Database URL (postgresql+asyncpg://...).
        config: Pool configuration (defaults to POOL_PRODUCTION).
        echo: Enable SQL query logging.
        **extra_kwargs: Additional kwargs for create_async_engine.

    Returns:
        Configured AsyncEngine.

    Raises:
        ImportError: If sqlalchemy[asyncio] is not installed.
        ValueError: If URL scheme is not supported.

    Example:
        ```python
        engine = create_async_engine(
            "postgresql+asyncpg://user:pass@localhost/mydb",
            PoolConfig(min_size=10, max_size=30),
        )
        ```
    """
    if not HAS_SQLALCHEMY:
        raise ImportError(
            "SQLAlchemy with asyncio support is required. "
            "Install with: pip install 'shared[db]'"
        )

    cfg = config or POOL_PRODUCTION

    # Build engine kwargs
    engine_kwargs = cfg.to_engine_kwargs()
    engine_kwargs["echo"] = echo

    # Add asyncpg-specific connect args for PostgreSQL
    if "asyncpg" in database_url:
        engine_kwargs["connect_args"] = cfg.to_connect_args()

    # For SQLite (testing), use NullPool
    if "sqlite" in database_url:
        engine_kwargs["poolclass"] = NullPool
        # Remove pool-specific settings that don't apply
        for key in ["pool_size", "max_overflow", "pool_timeout", "pool_recycle"]:
            engine_kwargs.pop(key, None)

    # Merge extra kwargs (allows overriding)
    engine_kwargs.update(extra_kwargs)

    logger.info(
        f"Creating async engine with pool: "
        f"size={cfg.min_size}, overflow={cfg.computed_overflow}, "
        f"timeout={cfg.pool_timeout}s, recycle={cfg.pool_recycle}s"
    )

    return _create_engine(database_url, **engine_kwargs)


def create_session_factory(
    engine: AsyncEngine,
    *,
    expire_on_commit: bool = False,
    autoflush: bool = True,
) -> async_sessionmaker[AsyncSession]:
    """
    Create an async session factory.

    Args:
        engine: AsyncEngine to bind sessions to.
        expire_on_commit: Whether to expire objects after commit.
        autoflush: Whether to autoflush before queries.

    Returns:
        Session factory that creates AsyncSession instances.

    Example:
        ```python
        SessionFactory = create_session_factory(engine)

        async with SessionFactory() as session:
            result = await session.execute(select(User))
            users = result.scalars().all()
        ```
    """
    if not HAS_SQLALCHEMY:
        raise ImportError(
            "SQLAlchemy with asyncio support is required. "
            "Install with: pip install 'shared[db]'"
        )

    return async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=expire_on_commit,
        autoflush=autoflush,
    )


@dataclass
class DatabaseHealthCheck:
    """
    Database health checker for readiness probes.

    Example:
        ```python
        health = DatabaseHealthCheck(engine)

        # In health endpoint
        if await health.is_healthy():
            return {"status": "healthy"}
        else:
            return {"status": "unhealthy"}, 503

        # Get detailed stats
        stats = await health.get_stats()
        ```
    """

    engine: AsyncEngine
    _last_check_time: float = field(default=0.0, init=False)
    _last_check_result: bool = field(default=False, init=False)
    _check_interval: float = 5.0  # Cache result for 5 seconds

    async def is_healthy(self, force: bool = False) -> bool:
        """
        Check if database is healthy.

        Args:
            force: Bypass cache and check immediately.

        Returns:
            True if healthy, False otherwise.
        """
        import time

        now = time.monotonic()

        # Return cached result if recent
        if not force and (now - self._last_check_time) < self._check_interval:
            return self._last_check_result

        try:
            async with self.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            self._last_check_result = True
        except Exception as e:
            logger.warning(f"Database health check failed: {e}")
            self._last_check_result = False

        self._last_check_time = now
        return self._last_check_result

    async def get_stats(self) -> dict[str, Any]:
        """
        Get pool statistics.

        Returns:
            Dict with pool size, overflow, checked out connections, etc.
        """
        pool = self.engine.pool
        return {
            "is_healthy": await self.is_healthy(),
            "pool_size": pool.size(),
            "checked_in": pool.checkedin(),
            "checked_out": pool.checkedout(),
            "overflow": pool.overflow(),
            "invalid": pool.invalidatedcount() if hasattr(pool, "invalidatedcount") else 0,
        }


@asynccontextmanager
async def get_db_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """
    Context manager for database sessions with automatic cleanup.

    Commits on success, rolls back on exception.

    Args:
        session_factory: Async session factory.

    Yields:
        AsyncSession for database operations.

    Example:
        ```python
        async with get_db_session(SessionFactory) as session:
            user = User(name="Alice")
            session.add(user)
            # Commits automatically on exit
        ```
    """
    session = session_factory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


# Predefined configs for common scenarios
POOL_DEVELOPMENT = PoolConfig(
    min_size=2,
    max_size=5,
    pool_timeout=10.0,
    pool_recycle=300,
    pool_pre_ping=True,
    echo_pool=True,
)

POOL_PRODUCTION = PoolConfig(
    min_size=5,
    max_size=20,
    pool_timeout=30.0,
    pool_recycle=3600,
    pool_pre_ping=True,
    echo_pool=False,
)

POOL_HIGH_LOAD = PoolConfig(
    min_size=20,
    max_size=100,
    pool_timeout=60.0,
    pool_recycle=1800,
    pool_pre_ping=True,
    echo_pool=False,
    command_timeout=60.0,
)
