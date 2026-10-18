"""Shared async fixtures with savepoint-isolated SQLite sessions.

Production pattern (SQLAlchemy 2.0 + pytest-asyncio 2025/2026):
- Bind AsyncSession to an open connection with an outer transaction
- join_transaction_mode=\"create_savepoint\" so service commit() releases a
  savepoint instead of ending the outer transaction
- Fixture teardown rolls back the outer transaction for isolation
- StaticPool keeps a single :memory: connection for schema + session
"""

from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Any

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from prime_agent.db import Base
from prime_agent.db.models.user import User
from prime_agent.services.auth_service import AuthService

PASSWORD = "SecurePass123!"


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record) -> None:
        # Required for reliable SAVEPOINT behavior on SQLite.
        dbapi_connection.isolation_level = None

    @event.listens_for(engine.sync_engine, "begin")
    def _do_begin(conn) -> None:
        conn.exec_driver_sql("BEGIN")

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with engine.connect() as connection:
        transaction = await connection.begin()
        session_factory = async_sessionmaker(
            bind=connection,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
            join_transaction_mode="create_savepoint",
        )
        async with session_factory() as session:
            yield session

        await transaction.rollback()

    await engine.dispose()


@pytest.fixture
def make_user(db_session: AsyncSession) -> Callable[..., Coroutine[Any, Any, User]]:
    async def _make_user(
        *,
        email: str = "user@example.com",
        name: str = "Test User",
        role: str = "member",
        is_active: bool = True,
        password: str = PASSWORD,
    ) -> User:
        account = User(
            email=email,
            name=name,
            hashed_password=AuthService.hash_password(password),
            role=role,
            is_active=is_active,
        )
        db_session.add(account)
        await db_session.commit()
        return account

    return _make_user


@pytest.fixture
async def user(make_user) -> User:
    return await make_user()
