"""MemoryView: a layer over a MemoryService that changes only what it overrides."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from supervisor.memory.service import CaptureContext, MemoryService
from supervisor.memory_service.notebooks.types import (
    Candidate,
    CaptureMetadata,
    Edit,
    Fact,
    RecallContext,
    Revision,
)
from supervisor.memory_service.observations import ObservationDraft

Rank = Callable[[Sequence[Fact], RecallContext | None], list[Candidate]]


class MemoryView:
    """Delegates every MemoryService call to ``base``; subclasses override selectively."""

    def __init__(self, base: MemoryService) -> None:
        self._base = base
        self.manages = base.manages
        self.rank: Rank | None = getattr(base, "rank", None)
        """The base's recall over facts already read, when it has one (``NotebookMemory.rank``)."""

    @property
    def base(self) -> MemoryService:
        return self._base

    async def recall(
        self, notebook_id: str, context: RecallContext | None = None
    ) -> list[Candidate]:
        return await self._base.recall(notebook_id, context)

    async def capture(
        self,
        notebook_id: str,
        facts: Sequence[str],
        *,
        author: str | None = None,
        context: CaptureContext | None = None,
    ) -> int:
        return await self._base.capture(notebook_id, facts, author=author, context=context)

    async def observe(
        self,
        notebook_id: str,
        drafts: Sequence[ObservationDraft],
        *,
        author: str | None = None,
        context: CaptureContext | None = None,
        idempotency_key: str | None = None,
    ) -> int | None:
        """Queue typed observations (G2), or None when the memory below takes plain
        captures only (``observe``)."""
        return await observe(
            self._base, notebook_id, drafts, author=author, context=context, idempotency_key=idempotency_key
        )

    async def query(
        self, notebook_id: str, q: str, limit: int = 20, context: RecallContext | None = None
    ) -> list[str]:
        return await self._base.query(notebook_id, q, limit, context)

    async def read(self, notebook_id: str) -> str:
        return await self._base.read(notebook_id)

    async def facts(self, notebook_id: str) -> list[Fact]:
        return await self._base.facts(notebook_id)

    async def edit(
        self,
        notebook_id: str,
        edits: Sequence[Edit],
        *,
        author: str | None = None,
        context: CaptureMetadata | None = None,
    ) -> None:
        await self._base.edit(notebook_id, edits, author=author, context=context)

    async def delete(
        self, notebook_id: str, ids: Sequence[str], *, author: str | None = None
    ) -> None:
        await self._base.delete(notebook_id, ids, author=author)

    async def clear(self, notebook_id: str, *, author: str | None = None) -> int:
        return await self._base.clear(notebook_id, author=author)

    async def history(self, notebook_id: str) -> list[Revision]:
        return await self._base.history(notebook_id)

    async def facts_at(self, notebook_id: str, revision: int) -> list[Fact]:
        return await self._base.facts_at(notebook_id, revision)

    async def restore(self, notebook_id: str, revision: int, *, author: str | None = None) -> None:
        await self._base.restore(notebook_id, revision, author=author)


async def observe(
    memory: MemoryService,
    notebook_id: str,
    drafts: Sequence[ObservationDraft],
    *,
    author: str | None = None,
    context: CaptureContext | None = None,
    idempotency_key: str | None = None,
) -> int | None:
    """Queue typed observations into ``memory`` (G2): the number queued, or None when it
    takes plain captures only (a provider's own memory, or a memory service older than G2),
    for the caller to capture their statements instead. Not part of ``MemoryService``, so a
    provider without it still is one."""
    queue = getattr(memory, "observe", None)
    if queue is None:
        return None
    queued: int | None = await queue(
        notebook_id, drafts, author=author, context=context, idempotency_key=idempotency_key
    )
    return queued
