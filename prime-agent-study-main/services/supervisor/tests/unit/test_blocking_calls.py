"""Long blocking calls run on a pool of their own (``supervisor.blocking``): many model
calls or tools at once neither queue behind the event loop's small default executor nor
keep it from resolving host names for new connections. The memory load test of
``docs/memory-gaps.md`` (G1) found both: sessions lost their memory when the memory
client could not connect in time."""

from __future__ import annotations

import asyncio
import contextvars
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from supervisor import blocking
from supervisor.blocking import run_blocking

pytestmark = pytest.mark.asyncio

DEFAULT_EXECUTOR = min(32, (os.cpu_count() or 1) + 4)
LONG = DEFAULT_EXECUTOR + 30
"""More long calls at once than the default executor has threads."""


async def test_many_long_calls_run_at_once_however_small_the_default_executor() -> None:
    release = threading.Event()
    running = 0
    peak = 0
    lock = threading.Lock()

    def model_call() -> str:
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        release.wait(10)
        with lock:
            running -= 1
        return "reply"

    calls = [asyncio.create_task(run_blocking(model_call)) for _ in range(LONG)]
    async with asyncio.timeout(10):
        while peak < LONG:
            await asyncio.sleep(0.01)
    release.set()

    assert await asyncio.gather(*calls) == ["reply"] * LONG
    assert peak == LONG, f"{LONG} long calls ran together, not {DEFAULT_EXECUTOR} at a time"


async def test_while_they_wait_the_loop_still_resolves_hosts_and_runs_short_work() -> None:
    release = threading.Event()
    calls = [asyncio.create_task(run_blocking(release.wait, 10)) for _ in range(LONG)]
    await asyncio.sleep(0.2)  # every long call holds its thread now
    try:
        began = time.perf_counter()
        resolved = await asyncio.get_running_loop().getaddrinfo("localhost", 8092)
        short = await asyncio.to_thread(lambda: 42)
        took = time.perf_counter() - began
    finally:
        release.set()
        await asyncio.gather(*calls)

    assert resolved and short == 42
    assert took < 1.0, f"a host lookup and a short call waited {took:.2f}s behind the long calls"


async def test_a_long_call_sees_the_callers_context() -> None:
    who: contextvars.ContextVar[str] = contextvars.ContextVar("who", default="nobody")
    who.set("session-7")

    assert await run_blocking(who.get) == "session-7"


async def test_arguments_and_errors_pass_through() -> None:
    def divide(a: int, b: int, *, scale: int = 1) -> float:
        return a / b * scale

    assert await run_blocking(divide, 6, 3, scale=10) == 20.0
    with pytest.raises(ZeroDivisionError):
        await run_blocking(divide, 1, 0)


def test_the_pool_is_sized_by_setting_and_a_bad_setting_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(blocking, "_pool", None)
    monkeypatch.setenv(blocking.BLOCKING_THREADS_ENV, "12")
    made: ThreadPoolExecutor = blocking.pool()
    assert made._max_workers == 12 and blocking.pool() is made  # type: ignore[attr-defined]

    monkeypatch.setattr(blocking, "_pool", None)
    for bad in ("0", "abc"):
        monkeypatch.setenv(blocking.BLOCKING_THREADS_ENV, bad)
        with pytest.raises(ValueError, match="BLOCKING_CALL_THREADS"):
            blocking.pool()
    monkeypatch.setattr(blocking, "_pool", None)
    monkeypatch.delenv(blocking.BLOCKING_THREADS_ENV)
    assert blocking.pool()._max_workers == blocking.DEFAULT_THREADS  # type: ignore[attr-defined]
    made.shutdown()


async def test_a_served_app_starts_with_its_loops_request_threads_widened() -> None:
    """Starlette's synchronous routes (the gateway's model calls) share anyio's limiter: 40
    threads a loop unless widened. ``WithRequestThreads`` widens it as the app starts, the
    app's own lifespan still runs inside, and the app itself is left as it was."""
    from contextlib import asynccontextmanager

    import anyio.to_thread
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from supervisor.main import WithRequestThreads

    started: list[int] = []

    @asynccontextmanager
    async def own(app: FastAPI):  # type: ignore[no-untyped-def]
        started.append(int(anyio.to_thread.current_default_thread_limiter().total_tokens))
        yield

    served = FastAPI(lifespan=own)
    lifespan = served.router.lifespan_context
    with TestClient(WithRequestThreads(WithRequestThreads(served))):  # wrapped twice, harmlessly
        pass

    assert started == [blocking.DEFAULT_THREADS], "widened before the app's own start, which ran once"
    assert served.router.lifespan_context is lifespan, "the app is not changed"


async def test_request_threads_are_set_to_the_setting_below_anyios_default_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """An operator may size a replica down as well as up: the setting is what is served."""
    import anyio.to_thread

    monkeypatch.setenv(blocking.BLOCKING_THREADS_ENV, "8")

    assert blocking.widen_request_threads() == 8
    assert anyio.to_thread.current_default_thread_limiter().total_tokens == 8


async def test_running_calls_are_counted_and_a_full_pool_is_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A call keeps its thread to its end, its caller cancelled or not, so the pool says
    how full it is: ``busy`` counts the calls running, and a full pool is logged."""
    monkeypatch.setattr(blocking, "_pool", None)
    monkeypatch.setattr(blocking, "_warned_at", float("-inf"))
    monkeypatch.setenv(blocking.BLOCKING_THREADS_ENV, "3")
    release = threading.Event()
    started = threading.Barrier(4)

    def hold() -> None:
        started.wait()
        release.wait(10)

    calls = [asyncio.create_task(blocking.run_blocking(hold)) for _ in range(3)]
    await asyncio.to_thread(started.wait)
    calls[0].cancel()  # its caller goes; its call keeps the thread

    assert blocking.busy() == 3
    assert "every one of the 3 long-call threads is busy" in caplog.text
    release.set()
    await asyncio.gather(*calls, return_exceptions=True)
    while blocking.busy():
        await asyncio.sleep(0.01)
    blocking.pool().shutdown()
