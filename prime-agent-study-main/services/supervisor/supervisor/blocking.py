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
``asyncio.to_thread``, the call sees the caller's context variables.

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
import os
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Final

import anyio.to_thread

__all__ = ["BLOCKING_THREADS_ENV", "DEFAULT_THREADS", "pool", "run_blocking", "widen_request_threads"]

BLOCKING_THREADS_ENV: Final = "BLOCKING_CALL_THREADS"
DEFAULT_THREADS: Final = 256

_pool: ThreadPoolExecutor | None = None
_lock = threading.Lock()


def _size() -> int:
    raw = os.environ.get(BLOCKING_THREADS_ENV, "").strip()
    if not raw:
        return DEFAULT_THREADS
    size = int(raw)  # a typo fails the process's first call loudly, not its throughput quietly
    if size < 1:
        raise ValueError(f"{BLOCKING_THREADS_ENV} must be 1 or more, not {size}")
    return size


def pool() -> ThreadPoolExecutor:
    """The process's pool for long blocking calls, made on first use (its threads start
    only as calls need them)."""
    global _pool
    with _lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(max_workers=_size(), thread_name_prefix="blocking")
        return _pool


async def run_blocking[T](func: Callable[..., T], /, *args: object, **kwargs: object) -> T:
    """``func(*args, **kwargs)`` on the long-call pool, with the caller's context
    variables: ``asyncio.to_thread`` for calls that wait a long time."""
    call = functools.partial(contextvars.copy_context().run, func, *args, **kwargs)
    return await asyncio.get_running_loop().run_in_executor(pool(), call)


def widen_request_threads() -> int:
    """Let the running loop's synchronous routes and streams use ``BLOCKING_CALL_THREADS``
    threads at once instead of anyio's 40; call it from an app's lifespan. The size set."""
    limiter = anyio.to_thread.current_default_thread_limiter()
    limiter.total_tokens = max(limiter.total_tokens, _size())
    return int(limiter.total_tokens)
