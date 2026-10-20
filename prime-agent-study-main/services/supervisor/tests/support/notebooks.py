"""Test databases, and notebooks as the supervisor reaches them: through the memory service.

Each test gets the real memory-service app, in-process (its lifespan started,
reached over ASGI), on its own database: SQLite always, and a fresh Postgres
database too when a test server is configured. Postgres is where row locks and
concurrent writers are real, so the store's guarantees are checked against it.
Requests carry the same credential the supervisor sends in production.
"""

from __future__ import annotations

import contextlib
import os
import socket
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from supervisor.db.engine import open_db
from supervisor.memory.client import NotebookClient
from supervisor.memory_service.app import create_app

PG_URL = os.environ.get("MEMORY_TEST_PG_URL") or os.environ.get("SUPERVISOR_TEST_PG_URL")

BACKENDS = [
    "sqlite",
    pytest.param("postgres", marks=pytest.mark.skipif(not PG_URL, reason="no Postgres test server")),
]



class MemoryServiceHandle:
    """One in-process memory service: make clients (one per "worker"), or reach its app."""

    def __init__(self, app: FastAPI) -> None:
        self.app = app
        self._made: list[NotebookClient] = []

    def client(self) -> NotebookClient:
        made = NotebookClient("http://memory.test", transport=httpx.ASGITransport(app=self.app))
        self._made.append(made)
        return made

    async def aclose(self) -> None:
        for made in self._made:
            await made.aclose()


@asynccontextmanager
async def _database(backend: str, tmp_path: Path) -> AsyncIterator[str]:
    if backend == "sqlite":
        yield f"sqlite+aiosqlite:///{tmp_path / 'memory.db'}"
        return
    assert PG_URL is not None
    name = f"notebooks_{uuid.uuid4().hex[:10]}"
    admin = create_async_engine(PG_URL, isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.execute(text(f'CREATE DATABASE "{name}"'))
    base, _, _ = PG_URL.rpartition("/")
    try:
        yield f"{base}/{name}"
    finally:
        async with admin.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        await admin.dispose()


@asynccontextmanager
async def memory_service(database_url: str) -> AsyncIterator[MemoryServiceHandle]:
    """The memory service on ``database_url``, running for the duration."""
    app = create_app(database_url=database_url)
    handle = MemoryServiceHandle(app)
    async with app.router.lifespan_context(app):
        try:
            yield handle
        finally:
            await handle.aclose()


@contextmanager
def served_memory_service(database_url: str) -> Iterator[str]:
    """The memory service over real HTTP on a free local port (its own thread and loop,
    as ``python -m supervisor.main`` serves it); yields its URL."""
    import threading

    import uvicorn

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    class _Server(uvicorn.Server):
        @contextlib.contextmanager
        def capture_signals(self) -> Iterator[None]:
            yield  # the test process keeps its own signal handling

    server = _Server(
        uvicorn.Config(create_app(database_url=database_url), host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, name="test-memory-service", daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise RuntimeError("the test memory service did not start")
        time.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=30)


@pytest.fixture
def memory_service_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """A live memory service for the test, found the way production finds it:
    ``MEMORY_SERVICE_URL`` (so supervisors built during the test, and worker
    processes they spawn, keep their notebooks there)."""
    with served_memory_service(f"sqlite+aiosqlite:///{tmp_path / 'memory-service.db'}") as url:
        monkeypatch.setenv("MEMORY_SERVICE_URL", url)
        yield url


@pytest_asyncio.fixture(params=BACKENDS)
async def supervisor_db(
    request: pytest.FixtureRequest, tmp_path: Path
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """The supervisor's own database for one test: SQLite, and a fresh Postgres one."""
    async with _database(request.param, tmp_path) as url, open_db(url) as (_, factory):
        yield factory


@pytest_asyncio.fixture(params=BACKENDS)
async def memory_svc(
    request: pytest.FixtureRequest, tmp_path: Path
) -> AsyncIterator[MemoryServiceHandle]:
    async with _database(request.param, tmp_path) as url, memory_service(url) as handle:
        yield handle


@pytest_asyncio.fixture
async def notebooks(memory_svc: MemoryServiceHandle) -> NotebookClient:
    return memory_svc.client()
