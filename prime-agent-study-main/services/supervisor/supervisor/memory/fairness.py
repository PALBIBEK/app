"""A cap on each tenant's capture model calls running at once, per worker (G2's scale rule
in ``docs/memory-gaps.md``).

Automatic capture makes model calls of its own (extraction, then resolution) for every
burst of every session. One tenant's burst (hundreds of sessions ending their turns at once)
would otherwise take every long-call thread (``supervisor.blocking``) and every capture slot,
and every other tenant's captures would wait behind it. A tenant gets at most
``MEMORY_CAPTURE_PER_TENANT`` (8) at once on a worker; the rest of its captures wait their
turn, and other tenants' go straight through.

Per worker process, and per event loop (a semaphore belongs to the loop that waits on it).
A cap shared by every worker would need shared state; per worker, a tenant's share grows
with the workers, as the platform's capacity does.
"""

from __future__ import annotations

import asyncio
import os
import weakref
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Final

__all__ = ["PER_TENANT_ENV", "GatedOneShot", "TenantGate", "capture_gate"]

PER_TENANT_ENV: Final = "MEMORY_CAPTURE_PER_TENANT"
DEFAULT_PER_TENANT: Final = 8


def _limit() -> int:
    raw = os.environ.get(PER_TENANT_ENV, "").strip()
    if not raw:
        return DEFAULT_PER_TENANT
    try:
        limit = int(raw)
    except ValueError:
        raise ValueError(f"{PER_TENANT_ENV} must be a whole number, not {raw!r}") from None
    if limit < 1:
        raise ValueError(f"{PER_TENANT_ENV} must be 1 or more, not {limit}")
    return limit


class TenantGate:
    """At most ``limit`` holders per tenant at once; a tenant's next holder waits for one of
    its own to leave, never for another tenant's."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self._lanes: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, _Lane]] = (
            weakref.WeakKeyDictionary()
        )

    def held(self, tenant: str) -> int:
        """Slots ``tenant`` holds now, on this loop."""
        lane = self._lanes.get(asyncio.get_running_loop(), {}).get(tenant)
        return lane.holding if lane is not None else 0

    def waiting(self, tenant: str) -> int:
        """``tenant``'s callers waiting for a slot now, on this loop."""
        lane = self._lanes.get(asyncio.get_running_loop(), {}).get(tenant)
        return lane.users - lane.holding if lane is not None else 0

    @asynccontextmanager
    async def slot(self, tenant: str) -> AsyncIterator[None]:
        lanes = self._lanes.setdefault(asyncio.get_running_loop(), {})
        lane = lanes.get(tenant) or lanes.setdefault(tenant, _Lane(asyncio.Semaphore(self.limit)))
        lane.users += 1
        try:
            async with lane.semaphore:
                lane.holding += 1
                try:
                    yield
                finally:
                    lane.holding -= 1
        finally:
            lane.users -= 1
            if not lane.users:
                lanes.pop(tenant, None)  # idle: nothing kept for every tenant ever seen


class _Lane:
    __slots__ = ("holding", "semaphore", "users")

    def __init__(self, semaphore: asyncio.Semaphore) -> None:
        self.semaphore = semaphore
        self.users = 0  # holding or waiting
        self.holding = 0


class GatedOneShot:
    """A one-shot whose every call takes one of its tenant's slots, for that call only: a
    capture sleeping between attempts (a rate limit, a pause) holds none. Keeps the model
    it names (``classification.one_shot_model``)."""

    def __init__(self, one_shot: Callable[[str, str], Awaitable[str]], tenant: str, gate: TenantGate) -> None:
        self._one_shot = one_shot
        self._tenant = tenant
        self._gate = gate
        self.model = getattr(one_shot, "model", None)

    async def __call__(self, system: str, content: str) -> str:
        """Waiting for a slot can be cancelled; the call, once made, holds its slot to its
        end: a gateway call runs on a thread no cancel stops (``supervisor.blocking``), so
        a cancelled caller freeing its slot early would let the tenant hold more threads
        than its slots."""
        held = AsyncExitStack()
        await held.enter_async_context(self._gate.slot(self._tenant))

        async def call() -> str:
            try:
                return await self._one_shot(system, content)
            finally:
                await held.aclose()

        made = asyncio.ensure_future(call())
        made.add_done_callback(_retrieved)  # a caller cancelled meanwhile reads no result
        return await asyncio.shield(made)


def _retrieved(task: asyncio.Future[str]) -> None:
    if not task.cancelled():
        task.exception()


_gate: TenantGate | None = None


def capture_gate() -> TenantGate:
    """The process's gate for capture's model calls, made on first use."""
    global _gate
    if _gate is None:
        _gate = TenantGate(_limit())
    return _gate
