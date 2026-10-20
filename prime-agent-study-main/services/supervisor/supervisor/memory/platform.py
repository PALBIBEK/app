"""MemoryPlatform: the single entry point to memory for the API, workers and tools.

It owns the store and the layered service (built-in notebooks, then semantic
ranking and provider routing when configured), and hands out per-audience views:

    view = platform.view(audience)      # disclosure ∘ classification ∘ base

A one-shot model call (classification, extraction, consolidation, titles) is
resolved per call, so a session can use its own model and everything else falls
back to the platform gateway; with no gateway at all, classification says
"unknown" (fail safe) and extraction finds nothing.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import replace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.db.models import ScopeMembershipRow, SessionRow
from supervisor.memory.binding import MemoryBinding
from supervisor.memory.classification import ClassifiedMemory, OneShot
from supervisor.memory.client import NotebookClient, UnavailableNotebooks
from supervisor.memory.disclosure import Audience, DisclosedMemory, IsMember
from supervisor.memory.service import MemoryService, NotebookMemory
from supervisor.memory_service.notebooks.types import scope_source
from supervisor.seams.core import factory
from supervisor.seams.core.types import ChatMessage

logger = logging.getLogger(__name__)


def gateway_one_shot(gateway: object, *, model: str | None = None) -> OneShot:
    """A OneShot over a ModelGatewayClient (blocking ``complete`` runs in a thread)."""

    async def call(system: str, content: str) -> str:
        completion = await asyncio.to_thread(
            gateway.complete,  # type: ignore[attr-defined]
            [ChatMessage("system", system), ChatMessage("user", content)],
            [],
            model=model,
        )
        message = getattr(completion, "message", None)
        return str(getattr(message, "content", "") or "")

    return call


def platform_one_shot() -> OneShot:
    """The platform gateway (GATEWAY_BASE_URL), resolved at call time."""

    async def call(system: str, content: str) -> str:
        gateway = factory._resolve_gateway(
            model_gateway=None, gateway_base_url=None, gateway_token=None
        )
        return await gateway_one_shot(gateway)(system, content)

    return call


class MemoryPlatform:
    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        *,
        is_member: IsMember | None = None,
        one_shot: OneShot | None = None,
        layers: Callable[[MemoryService], MemoryService] | None = None,
        notebooks: NotebookClient | None = None,
    ) -> None:
        """``notebooks``: the memory service's notebooks (default ``MEMORY_SERVICE_URL``).

        Without one, long-term memory is unavailable and says so when used: there
        is no second, local store to fall back on.
        """
        self._db = factory
        self.store: NotebookClient | UnavailableNotebooks = (
            notebooks or NotebookClient.from_env() or UnavailableNotebooks()
        )
        self.notebooks = NotebookMemory(self.store)
        self.base: MemoryService = layers(self.notebooks) if layers else self.notebooks
        self._is_member = is_member or self._db_membership
        self._one_shot = one_shot or platform_one_shot()

    @property
    def one_shot(self) -> OneShot:
        return self._one_shot

    def view(self, audience: Audience, *, one_shot: OneShot | None = None) -> MemoryService:
        """What ``audience`` may see, with explicit writes classified by ``one_shot``."""
        if audience.is_member is None:
            audience = replace(audience, is_member=self._is_member)
        classified = ClassifiedMemory(self.base, one_shot or self._one_shot)
        return DisclosedMemory(classified, audience, self._home_of)

    def private(self, principal_id: str, *, one_shot: OneShot | None = None) -> MemoryService:
        return self.view(Audience.private(principal_id), one_shot=one_shot)

    async def session_audience(self, session_id: str) -> tuple[Audience, MemoryBinding] | None:
        """The audience a session speaks to (its owner, or its scope's members)."""
        async with self._db() as db:
            row = await db.get(SessionRow, session_id)
            if row is None or not row.memory:
                return None
            binding = MemoryBinding.from_dict(row.memory)
            if row.scope_id is None:
                private = Audience.private(binding.owner)
                return replace(private, is_member=self._is_member, open=binding.open), binding
            members = tuple(
                await db.scalars(
                    select(ScopeMembershipRow.principal_id).where(
                        ScopeMembershipRow.scope_id == row.scope_id
                    )
                )
            )
        return (
            Audience(
                binding.owner,
                scope_source(row.scope_id),
                tuple(sorted({binding.owner, *members})),
                self._is_member,
                binding.open,
            ),
            binding,
        )

    async def _home_of(self, notebook_id: str) -> str:
        return (await self.store.get(notebook_id)).home

    async def _db_membership(self, principal_id: str, scope_id: str) -> bool:
        async with self._db() as db:
            found = await db.scalar(
                select(ScopeMembershipRow.principal_id).where(
                    ScopeMembershipRow.scope_id == scope_id,
                    ScopeMembershipRow.principal_id == principal_id,
                )
            )
            return found is not None
