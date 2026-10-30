"""Alembic environment for the product's schema (``prime_agent.db.migrate`` runs it at boot).

The database comes from, most specific first: a connection handed in by the caller (the
boot path, under a lock), ``-x url=...``, then ``DATABASE_URL``. Alembic runs on an async
engine through ``run_sync``, so no second, synchronous driver is needed.
"""

from __future__ import annotations

import asyncio
import os

from alembic import context
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from prime_agent.db import (
    Base,
    models,  # noqa: F401  (registers every table on Base.metadata)
)

target_metadata = Base.metadata


def _configure(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)


def _run(connection: Connection) -> None:
    _configure(connection)
    with context.begin_transaction():
        context.run_migrations()


async def _run_with_url(url: str) -> None:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            await conn.run_sync(_run)
            await conn.commit()
    finally:
        await engine.dispose()


def run_migrations_online() -> None:
    connection = context.config.attributes.get("connection")
    if connection is not None:
        _run(connection)
        return
    url = context.get_x_argument(as_dictionary=True).get("url") or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("no database: pass -x url=postgresql://... or set DATABASE_URL")
    if url.startswith(("postgresql://", "postgres://")):  # the async driver, as the app uses
        url = "postgresql+asyncpg://" + url.split("://", 1)[1]
    asyncio.run(_run_with_url(url))


if context.is_offline_mode():
    raise RuntimeError("offline (SQL script) migrations are not supported")
run_migrations_online()
