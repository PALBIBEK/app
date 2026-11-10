"""Many replicas of the memory service on one Postgres database, and workers that really
overlap: the harness for memory's concurrency tests (``docs/memory-gaps.md``, G0).

The platform runs many agents and many concurrent sessions, on any worker, against
memory-service replicas that share one Postgres. A test of a memory guarantee is only
worth its name when it holds there, so:

* **Postgres only.** SQLite serializes writers in one file and has no ``FOR UPDATE SKIP
  LOCKED``, so a race run on it proves nothing. Without a test server
  (``MEMORY_TEST_PG_URL`` or ``SUPERVISOR_TEST_PG_URL``) the tests skip, unless
  ``MEMORY_CONCURRENCY_REQUIRED`` is set (CI's ``tests`` job sets it beside its server):
  then they fail, so that job cannot pass by skipping.
* **Within the server's connections.** Each replica's pool is small
  (``MEMORY_DB_POOL_SIZE`` / ``MEMORY_DB_MAX_OVERFLOW``: 2 + 2), so four test workers, each
  with up to eight replicas, stay under a default server's 100 connections; writers still
  contend, on the rows, as production replicas do.
* **Real replicas.** Each replica is its own memory-service app: its own engine and
  connection pool and its own background loops (extraction worker, buffer sweeper,
  archiver), on the shared database, as production replicas are. Replicas start together, so their schema bootstrap races too.
* **Real overlap.** ``run_workers`` releases every worker at one gate and waits for all of
  them, so they contend instead of running one after another.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack

import pytest
import pytest_asyncio
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import create_async_engine

from supervisor.memory.client import NotebookClient
from tests.support.notebooks import MemoryServiceHandle, memory_service

PG_URL = os.environ.get("MEMORY_TEST_PG_URL") or os.environ.get("SUPERVISOR_TEST_PG_URL") or ""
POOL = {"MEMORY_DB_POOL_SIZE": "2", "MEMORY_DB_MAX_OVERFLOW": "2"}
"""Each replica's connections in these tests (see the module's docstring)."""


class Replicas:
    """Memory-service replicas on one fresh Postgres database (``url``)."""

    def __init__(self, url: str, stack: AsyncExitStack) -> None:
        self.url = url
        self._stack = stack
        self.handles: list[MemoryServiceHandle] = []

    async def start(self, n: int) -> list[MemoryServiceHandle]:
        """Start ``n`` more replicas at once; every replica started so far is kept running."""
        started = await asyncio.gather(
            *(self._stack.enter_async_context(memory_service(self.url)) for _ in range(n)), return_exceptions=True
        )
        # Every boot ends before a failure is raised: none is left starting into teardown, and
        # every one that started is on the stack to be stopped.
        failures = [s for s in started if isinstance(s, BaseException)]
        if failures:
            raise BaseExceptionGroup(f"{len(failures)} of {n} replicas did not start", failures)
        handles = [s for s in started if not isinstance(s, BaseException)]
        self.handles.extend(handles)
        return handles

    def clients(self, n: int) -> list[NotebookClient]:
        """``n`` clients spread round-robin over the running replicas: one per worker."""
        if not self.handles:
            raise RuntimeError("start replicas before asking for clients")
        return [self.handles[i % len(self.handles)].client() for i in range(n)]


def _require_server() -> str:
    if not PG_URL:
        if os.environ.get("MEMORY_CONCURRENCY_REQUIRED", "").strip().lower() in ("1", "true", "yes"):
            pytest.fail("memory concurrency tests need Postgres: set SUPERVISOR_TEST_PG_URL")
        pytest.skip("no Postgres test server (set MEMORY_TEST_PG_URL or SUPERVISOR_TEST_PG_URL)")
    return PG_URL


@pytest_asyncio.fixture
async def replicas(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Replicas]:
    """A fresh Postgres database for one test, and the replicas it starts on it."""
    server = _require_server()
    for name, value in POOL.items():
        monkeypatch.setenv(name, value)
    name = f"memrace_{uuid.uuid4().hex[:10]}"
    admin = create_async_engine(server, isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            await conn.execute(text(f'CREATE DATABASE "{name}"'))
        try:
            async with AsyncExitStack() as stack:
                yield Replicas(make_url(server).set(database=name).render_as_string(hide_password=False), stack)
        finally:
            async with admin.connect() as conn:
                await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    finally:  # disposed even when the database could not be created
        await admin.dispose()


async def run_workers[T](n: int, work: Callable[[int], Awaitable[T]]) -> list[T]:
    """``work(0)`` … ``work(n - 1)``, released together at one gate; their results in order.

    Every worker runs to its end even when another fails, so a failure never leaves work
    running into the next test; then the failures are raised together.
    """
    gate = asyncio.Event()

    async def one(i: int) -> T:
        await gate.wait()
        return await work(i)

    tasks = [asyncio.create_task(one(i)) for i in range(n)]
    await asyncio.sleep(0)  # every task is now parked at the gate
    gate.set()
    done = await asyncio.gather(*tasks, return_exceptions=True)
    failures = [d for d in done if isinstance(d, BaseException)]
    if failures:
        raise BaseExceptionGroup(f"{len(failures)} of {n} workers failed", failures)
    return [d for d in done if not isinstance(d, BaseException)]
