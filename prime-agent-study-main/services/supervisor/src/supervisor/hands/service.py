"""Each session's sandbox on this worker: built on demand, replaced after a loss."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from typing import TYPE_CHECKING, Protocol

from supervisor.hands.keys import split_key
from supervisor.hands.recipe import HandRecipe

if TYPE_CHECKING:
    from supervisor.sandbox.base import Sandbox

logger = logging.getLogger(__name__)

__all__ = ["HandEvents", "SessionHands"]


class HandEvents(Protocol):
    """What the session layer does around a sandbox's life (journal, files)."""

    async def hydrate(self, session_id: str, sandbox: Sandbox) -> None:
        """Put the session's saved files into a sandbox that is about to serve it."""
        ...

    async def provisioned(self, session_id: str, sandbox: Sandbox, cause: str) -> None: ...

    async def lost(self, session_id: str, sandbox: Sandbox, reason: str) -> None: ...


class SessionHands:
    """The sandboxes this worker holds, one per session at most.

    A session is *registered* with its recipe (``None``: its harness runs no
    sandbox) when it is created or resumed here; its sandbox exists only between
    ``ensure`` (first need) and ``lose`` / ``release``. All methods run on the
    supervisor's event loop: building a sandbox and hydrating it touch the
    database, which tool code (on its own loop) must never do.
    """

    __slots__ = ("_events", "_locks", "_recipes", "_replaced", "live")

    def __init__(self, events: HandEvents) -> None:
        self._events = events
        self._recipes: dict[str, HandRecipe | None] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._replaced: set[str] = set()
        self.live: dict[str, Sandbox] = {}
        """Sandboxes currently serving a session, by session id."""

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

    # --- life cycle -------------------------------------------------------------

    async def ensure(self, session_id: str, *, cause: str | None = None) -> Sandbox | None:
        """The session's sandbox, building (and hydrating) one if none is serving it.

        ``None`` when the session's harness runs no sandbox. Concurrent callers
        share one build. A build whose hydration fails is destroyed and the error
        raised: a sandbox without the session's files must never serve it.
        """
        if (current := self.live.get(session_id)) is not None:
            return current
        recipe = self._recipes.get(session_id)
        if recipe is None:
            return None
        async with self._locks.setdefault(session_id, asyncio.Lock()):
            if (current := self.live.get(session_id)) is not None:
                return current
            sandbox = await recipe.build()
            try:
                await self._events.hydrate(session_id, sandbox)
            except BaseException:
                await _destroy(sandbox, session_id)
                raise
            self.live[session_id] = sandbox
            why = cause or ("replaced" if session_id in self._replaced else "first_use")
            self._replaced.discard(session_id)
            await self._events.provisioned(session_id, sandbox, why)
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
            await self._events.hydrate(session_id, adopted)
            self.live[session_id] = adopted
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
        await _destroy(current, session_id)
        await self._events.lost(session_id, current, reason)
        return True

    def detach(self, session_id: str) -> Sandbox | None:
        """Stop tracking the session's sandbox (the caller pauses or destroys it)."""
        return self.live.pop(session_id, None)

    def release(self, session_id: str) -> Sandbox | None:
        """Forget the session entirely; returns its sandbox for the caller to destroy."""
        self._recipes.pop(session_id, None)
        self._locks.pop(session_id, None)
        self._replaced.discard(session_id)
        return self.live.pop(session_id, None)


async def _destroy(sandbox: Sandbox, session_id: str) -> None:
    try:
        await sandbox.destroy()
    except Exception:  # a dead sandbox may refuse; it is let go either way
        logger.warning(
            "destroying sandbox %s failed session=%s", sandbox.id, session_id, exc_info=True
        )
