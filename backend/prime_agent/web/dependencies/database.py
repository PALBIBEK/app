"""
Database dependency for FastAPI routes.

Provides async database session management for route handlers.
"""
from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from prime_agent.db.session import get_db as _get_db


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency for database session.

    Provides an async database session to route handlers.
    Services commit successful writes; failed requests are rolled back.

    Usage:
        @router.get("/users")
        async def list_users(db: AsyncSession = Depends(get_db)):
            result = await db.execute(select(User))
            return result.scalars().all()

    Yields:
        AsyncSession: Database session
    """
    async for session in _get_db():
        yield session

DbSession = Annotated[AsyncSession, Depends(get_db)]
