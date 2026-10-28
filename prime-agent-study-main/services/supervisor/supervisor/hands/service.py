"""Each session's sandbox on this worker: built on demand (or as a run begins), replaced
after a loss, stopped when idle and restored on the next need."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
from collections.abc import Iterator
from typing import TYPE_CHECKING, Protocol

from supervisor.hands.keys import split_key
from supervisor.hands.recipe import HandRecipe

if TYPE_CHECKING:
    from supervisor.sandbox.base import Sandbox

logger = logging.getLogger(__name__)

__all__ = ["HandEvents", "SessionHands"]

_LOGS_TIMEOUT_S = 10.0
"""A lost sandbox's logs are read for at most this long; the loss does not wait on a hung provider."""


class HandEvents(Protocol):
    """What the session layer does around a sandbox's life (journal, files)."""

    async def hydrate(self, session_id: str, sandbox: Sandbox, *, adopted: bool = False) -> None:
        """Put the session's saved files into a sandbox that is about to serve it.

        ``adopted``: a sandbox a dead worker left, still alive; its files are newer than
        any checkpoint (a tool may have ended after the last one), so none is restored.
        """
        ...

    async def provisioned(self, session_id: str, sandbox: Sandbox, cause: str) -> None: ...

    async def lost(self, session_id: str, sandbox: Sandbox, reason: str, logs: bytes | None) -> None:
        """``sandbox`` died; ``logs`` are what its provider recorded, read before it was destroyed."""
        ...


class SessionHands:
    """The sandboxes this worker holds, one per session at most.

    A session is *registered* with its recipe (``None``: its harness runs no
    sandbox) when it is created or resumed here; its sandbox exists only between
    ``ensure`` (first need) and ``lose`` / ``release``. All methods run on the
    supervisor's event loop: building a sandbox and hydrating it touch the
    database, which tool code (on its own loop) must never do.
    """

    __slots__ = ("_events", "_locks", "_recipes", "_replaced", "_stopped", "_using", "last_used", "live", "prewarming")

    def __init__(self, events: HandEvents) -> None:
        self._events = events
        self._recipes: dict[str, HandRecipe | None] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._replaced: set[str] = set()
        self._stopped: set[str] = set()
        self._using: Counter[str] = Counter()
        self.live: dict[str, Sandbox] = {}
        """Sandboxes currently serving a session, by session id."""
        self.last_used: dict[str, float] = {}
        """When each live sandbox was last built or used (``time.monotonic``)."""
        self.prewarming: dict[str, asyncio.Task[Sandbox | None]] = {}
        """Builds started as a run began, before any tool asked (``prewarm``)."""

    def __contains__(self, session_id: object) -> bool:
        return session_id in self._recipes

    def __iter__(self) -> Iterator[str]:
        return iter(self._recipes)

    # --- registration -----------------------------------------------------------

    def register(self, session_id: str, recipe: HandRecipe | None) -> None:
        self._recipes[session_id] = recipe

    def recipe(self, session_id: str) -> HandRecipe | None:
        return self._recipes.get(session_id)

    def keys_of(self, session_id: str) -> list[str]:
        """The keys of every hand registered for ``session_id``: its default hand first."""
        return sorted(
            (key for key in self._recipes if split_key(key)[0] == session_id),
            key=lambda key: (key != session_id, key),
        )

    def sandbox(self, session_id: str) -> Sandbox | None:
        return self.live.get(session_id)

    def attach(self, session_id: str, sandbox: Sandbox) -> None:
        """A sandbox found after a restart (the orphan reaper) serves its session again."""
        self.live[session_id] = sandbox
        self.last_used[session_id] = time.monotonic()

    # --- life cycle -------------------------------------------------------------

    async def ensure(self, session_id: str, *, cause: str | None = None) -> Sandbox | None:
        """The session's sandbox, building (and hydrating) one if none is serving it.

        ``None`` when the session's harness runs no sandbox. Concurrent callers
        share one build. A build whose hydration fails is destroyed and the error
        raised: a sandbox without the session's files must never serve it.
        """
        if (current := self.live.get(session_id)) is not None:
            self.last_used[session_id] = time.monotonic()
            return current
        recipe = self._recipes.get(session_id)
        if recipe is None:
            return None
        async with self._locks.setdefault(session_id, asyncio.Lock()):
            if (current := self.live.get(session_id)) is not None:
                self.last_used[session_id] = time.monotonic()
                return current
            sandbox = await recipe.build()
            try:
                await self._events.hydrate(session_id, sandbox)
            except BaseException:
                await _destroy(sandbox, session_id)
                raise
            self.live[session_id] = sandbox
            self.last_used[session_id] = time.monotonic()
            why = cause or (
                "replaced" if session_id in self._replaced
                else "resumed" if session_id in self._stopped
                else "first_use"
            )
            if cause == "prewarm" and session_id in self._stopped:
                why = "prewarm_resumed"
            self._replaced.discard(session_id)
            self._stopped.discard(session_id)
            await self._events.provisioned(session_id, sandbox, why)
            return sandbox

    def prewarm(self, session_id: str) -> asyncio.Task[Sandbox | None] | None:
        """Start building the session's sandbox now, without waiting for it.

        For a run that is beginning: the build overlaps the first model call, and the
        first tool call shares it (``ensure`` holds the same lock). None when nothing
        is to be built (no sandbox for this harness, one already serves, one is building).
        A failed build is only logged: the tool call that needs it tries again and tells
        the model if it cannot.
        """
        if self._recipes.get(session_id) is None or session_id in self.live:
            return None
        if (running := self.prewarming.get(session_id)) is not None and not running.done():
            return None

        async def build() -> Sandbox | None:
            try:
                return await self.ensure(session_id, cause="prewarm")
            except Exception:
                logger.warning("prewarming a sandbox failed session=%s", session_id, exc_info=True)
                return None
            finally:
                self.prewarming.pop(session_id, None)

        task = asyncio.create_task(build(), name=f"prewarm:{session_id}")
        self.prewarming[session_id] = task
        return task

    # --- in use, idle, stopped ----------------------------------------------------

    def begin_use(self, session_id: str) -> None:
        """A tool is about to act in the session's sandbox: never stop it under the call."""
        self._using[session_id] += 1
        self.last_used[session_id] = time.monotonic()

    def end_use(self, session_id: str) -> None:
        if self._using[session_id] > 0:
            self._using[session_id] -= 1
        if self._using[session_id] == 0:
            del self._using[session_id]
        self.last_used[session_id] = time.monotonic()

    def in_use(self, session_id: str) -> bool:
        return self._using.get(session_id, 0) > 0

    def idle_for(self, session_id: str, now: float | None = None) -> float:
        """Seconds since the session's sandbox was last used (0 while a tool uses it)."""
        if self.in_use(session_id) or session_id not in self.live:
            return 0.0
        return (now if now is not None else time.monotonic()) - self.last_used.get(session_id, time.monotonic())

    def stop(self, session_id: str) -> Sandbox | None:
        """Stop tracking an idle sandbox (the caller saved it first and destroys it); the
        next ``ensure`` builds a fresh one and restores the saved files into it."""
        sandbox = self.live.pop(session_id, None)
        if sandbox is not None:
            self._stopped.add(session_id)
            self.last_used.pop(session_id, None)
        return sandbox

    async def adopt(self, session_id: str) -> Sandbox | None:
        """Take over the sandbox a dead worker left, if it is still alive (F21)."""
        recipe = self._recipes.get(session_id)
        if recipe is None or session_id in self.live:
            return self.live.get(session_id)
        async with self._locks.setdefault(session_id, asyncio.Lock()):
            if session_id in self.live:
                return self.live[session_id]
            adopted = await recipe.adopt(session_id)
            if adopted is None:
                return None
            await self._events.hydrate(session_id, adopted, adopted=True)
            self.live[session_id] = adopted
            self.last_used[session_id] = time.monotonic()
            logger.info(
                "adopted %s sandbox %s session=%s", recipe.provider_name, adopted.id, session_id
            )
            await self._events.provisioned(session_id, adopted, "adopted")
            return adopted

    async def lose(self, session_id: str, hand_id: str, reason: str) -> bool:
        """``hand_id`` died under the session: let it go so the next call gets a fresh one.

        False when it no longer serves the session (another call already let it go).
        """
        current = self.live.get(session_id)
        if current is None or current.id != hand_id:
            return False
        self.live.pop(session_id, None)
        self._replaced.add(session_id)
        logs = await _logs(current, session_id)
        await _destroy(current, session_id)
        await self._events.lost(session_id, current, reason, logs)
        return True

    def detach(self, session_id: str) -> Sandbox | None:
        """Stop tracking the session's sandbox (the caller pauses or destroys it)."""
        self.last_used.pop(session_id, None)
        return self.live.pop(session_id, None)

    def release(self, session_id: str) -> Sandbox | None:
        """Forget the session entirely; returns its sandbox for the caller to destroy."""
        self._recipes.pop(session_id, None)
        self._locks.pop(session_id, None)
        self._replaced.discard(session_id)
        self._stopped.discard(session_id)
        self._using.pop(session_id, None)
        self.last_used.pop(session_id, None)
        if (building := self.prewarming.pop(session_id, None)) is not None:
            building.cancel()
        return self.live.pop(session_id, None)


async def _logs(sandbox: Sandbox, session_id: str) -> bytes | None:
    try:
        return await asyncio.wait_for(sandbox.logs(), timeout=_LOGS_TIMEOUT_S)
    except Exception:  # the loss is recorded with or without them
        logger.warning("reading lost sandbox %s's logs failed session=%s", sandbox.id, session_id, exc_info=True)
        return None


async def _destroy(sandbox: Sandbox, session_id: str) -> None:
    try:
        await sandbox.destroy()
    except Exception:  # a dead sandbox may refuse; it is let go either way
        logger.warning(
            "destroying sandbox %s failed session=%s", sandbox.id, session_id, exc_info=True
        )
