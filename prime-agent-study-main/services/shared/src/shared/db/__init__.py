"""
Database utilities for production-grade applications.

Provides:
- Connection pool configuration
- Async engine factory with health checks
- Session management with scoped lifetime

Usage:
    from shared.db import (
        PoolConfig,
        create_async_engine,
        create_session_factory,
    )

    # Create engine with production pool settings
    engine = create_async_engine(
        "postgresql+asyncpg://user:pass@host/db",
        PoolConfig(min_size=10, max_size=50),
    )

    # Create session factory
    SessionFactory = create_session_factory(engine)

    # Use in request handler
    async with SessionFactory() as session:
        result = await session.execute(query)
"""

from shared.db.pool import (
    PoolConfig,
    create_async_engine,
    create_session_factory,
    DatabaseHealthCheck,
    POOL_DEVELOPMENT,
    POOL_PRODUCTION,
    POOL_HIGH_LOAD,
)

__all__ = [
    "PoolConfig",
    "create_async_engine",
    "create_session_factory",
    "DatabaseHealthCheck",
    "POOL_DEVELOPMENT",
    "POOL_PRODUCTION",
    "POOL_HIGH_LOAD",
]
