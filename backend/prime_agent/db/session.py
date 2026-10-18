"""Async database session management."""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from prime_agent.db import Base


class DatabaseSession:
    """Lazy-initialized async engine + session factory."""

    def __init__(self) -> None:
        self._engine: AsyncEngine | None = None
        self._session_factory: async_sessionmaker[AsyncSession] | None = None

    def init(self, database_url: str, echo: bool = False) -> None:
        self._engine = create_async_engine(
            database_url,
            echo=echo,
            pool_pre_ping=True,
            pool_size=20,
            max_overflow=20,
            pool_recycle=3600,
        )
        self._session_factory = async_sessionmaker(
            bind=self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )

    async def create_tables(self) -> None:
        if self._engine is None:
            raise RuntimeError("DatabaseSession not initialized")
        # Import models so metadata is populated
        from prime_agent.db import models  # noqa: F401

        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def dispose(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
            self._session_factory = None

    async def get_session(self) -> AsyncGenerator[AsyncSession, None]:
        if self._session_factory is None:
            raise RuntimeError("DatabaseSession not initialized")
        async with self._session_factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise


db = DatabaseSession()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding an AsyncSession."""
    async for session in db.get_session():
        yield session
