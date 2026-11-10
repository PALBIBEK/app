"""Long blocking calls on threads of their own: a model's reply, a tool's command.

``asyncio.to_thread`` runs a call on the event loop's default executor, which has
``min(32, CPUs + 4)`` threads (six on a two-CPU host), and the loop uses that executor for
its own work too: resolving a host name for every new connection (``getaddrinfo``). A
model call holds its thread for as long as the model takes (tens of seconds) and a tool
for as long as its command runs, so a worker holding many sessions:

* ran at most that many model calls or tools at once, however many runs it admitted
  (``WORKER_MAX_CONCURRENT_RUNS``, 50), the rest queued behind them; and
* could not resolve a host while they ran: every new connection (to the memory service,
  say) waited for a thread and timed out connecting, its client's breaker opened, and
  sessions lost their memory. The memory load test found it (``docs/memory-gaps.md``, G1).

``run_blocking`` runs such a call on a pool sized for calls that wait, not for CPU
(``BLOCKING_CALL_THREADS``, 256 by default: a waiting thread costs a little memory, no
CPU), and leaves the default executor to short work and the loop's own. Like
``asyncio.to_thread``, the call sees the caller's context variables, and cancelling the
caller does not stop a call already running: its thread runs it to its end (every call
sent here is bounded by its own timeout). The pool is made, and its size read, once per
process, on first use, and lives as long as the process. ``busy`` counts the calls
running on it (a stopped run's still-running call included) and those queued for a
thread, and a full pool is logged (once a minute at most): the size is a setting, and saturation says to raise it.

The served apps have the same limit one layer up: Starlette runs each synchronous route
(the model gateway's every model call) and each read of a synchronous streamed response
on anyio's thread limiter, 40 per event loop, so one gateway replica served at most 40
generations at once, whatever its callers asked. ``widen_request_threads``, called in an
app's lifespan (its own loop), gives it the same size.
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import logging
import os
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Final

import anyio.to_thread

__all__ = ["BLOCKING_THREADS_ENV", "DEFAULT_THREADS", "busy", "pool", "run_blocking", "threads", "widen_request_threads"]

BLOCKING_THREADS_ENV: Final = "BLOCKING_CALL_THREADS"
DEFAULT_THREADS: Final = 256

FULL_WARNING_INTERVAL_S: Final = 60.0
"""How often, at most, a full pool is logged."""

_pool: ThreadPoolExecutor | None = None
_pool_size = 0
_lock = threading.Lock()
_running = 0
_queued = 0
_warned_at = float("-inf")
logger = logging.getLogger(__name__)


def threads() -> int:
    """``BLOCKING_CALL_THREADS``, checked: raises, naming it, when it is not a whole number
    of 1 or more. Read it where a process starts, so a typo stops the process there."""
    raw = os.environ.get(BLOCKING_THREADS_ENV, "").strip()
    if not raw:
        return DEFAULT_THREADS
    # A typo fails loudly (the app does not start, or the first call fails), never by
    # quietly running at a size nobody chose.
    try:
        size = int(raw)
    except ValueError:
        raise ValueError(f"{BLOCKING_THREADS_ENV} must be a whole number of threads, not {raw!r}") from None
    if size < 1:
        raise ValueError(f"{BLOCKING_THREADS_ENV} must be 1 or more, not {size}")
    return size


def pool() -> ThreadPoolExecutor:
    """The process's pool for long blocking calls, made on first use (its threads start
    only as calls need them)."""
    global _pool, _pool_size
    with _lock:
        if _pool is None:
            _pool_size = threads()
            _pool = ThreadPoolExecutor(max_workers=_pool_size, thread_name_prefix="blocking")
        return _pool


def busy() -> tuple[int, int]:
    """(running, queued): the calls on the pool's threads now, those whose callers were
    cancelled included, and the calls waiting for a thread. Queued calls mean the pool is
    full: every further model call and tool waits."""
    with _lock:
        return _running, _queued


def _counted[T](func: Callable[[], T]) -> T:
    """``func()``, counted as running (no longer queued) while it runs on its thread."""
    global _running, _queued
    with _lock:
        _queued -= 1
        _running += 1
    try:
        return func()
    finally:
        with _lock:
            _running -= 1


def _queue_one() -> None:
    """Count a call submitted, and log (once a minute at most) when it must wait."""
    global _queued, _warned_at
    with _lock:
        _queued += 1
        running, queued, size = _running, _queued, _pool_size
        warn = running + queued > size and time.monotonic() - _warned_at >= FULL_WARNING_INTERVAL_S
        if warn:
            _warned_at = time.monotonic()
    if warn:
        logger.warning(
            "all %d long-call threads are busy (calls waiting for one: %d): model calls and tools "
            "queue until a thread frees (raise %s)", running, running + queued - size, BLOCKING_THREADS_ENV,
        )


async def run_blocking[T](func: Callable[..., T], /, *args: object, **kwargs: object) -> T:
    """``func(*args, **kwargs)`` on the long-call pool, with the caller's context
    variables: ``asyncio.to_thread`` for calls that wait a long time."""
    call = functools.partial(contextvars.copy_context().run, func, *args, **kwargs)
    executor = pool()
    _queue_one()
    try:
        submitted = executor.submit(_counted, call)
    except BaseException:
        _unqueue()
        raise
    # A call cancelled before a thread took it never runs ``_counted``: it leaves the queue here.
    submitted.add_done_callback(lambda done: _unqueue() if done.cancelled() else None)
    return await asyncio.wrap_future(submitted)


def _unqueue() -> None:
    global _queued
    with _lock:
        _queued -= 1


def widen_request_threads(size: int | None = None) -> int:
    """Let the running loop's synchronous routes and streams use ``BLOCKING_CALL_THREADS``
    threads at once instead of anyio's 40; call it as an app starts. The size set.

    Set, not raised: an operator may size a replica below 40 as well as above. Each served
    app has a loop, and so a limiter, of its own (``supervisor.main.serve``). The setting
    is checked before this is reached (``threads``, as each server is built): raised in a
    lifespan, uvicorn would take the failure for "lifespan unsupported" and serve the app
    without its startup."""
    limiter = anyio.to_thread.current_default_thread_limiter()
    limiter.total_tokens = threads() if size is None else size
    return int(limiter.total_tokens)
