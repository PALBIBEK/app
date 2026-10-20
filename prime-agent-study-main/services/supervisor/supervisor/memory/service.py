"""The memory service: what recall, tools, capture and the API call (QM's MemoryService).

``NotebookMemory`` is the built-in implementation, over the memory service's notebooks. Views
(disclosure, classification, semantic ranking, provider routing) wrap a service
and return another, so callers never see which layers are present.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from supervisor.memory.client import NotebookClient, UnavailableNotebooks
from supervisor.memory_service.notebooks.render import (
    clean_captured,
    query_facts,
    recent_candidates,
    render_facts,
)
from supervisor.memory_service.notebooks.types import (
    Candidate,
    CaptureMetadata,
    Edit,
    Fact,
    RecallContext,
    Revision,
)

type CaptureMode = Literal["explicit", "automatic"]


@dataclass(frozen=True, slots=True)
class CaptureContext(CaptureMetadata):
    """A capture's runtime context: how it was triggered, by whom, from what exchange."""

    mode: CaptureMode = "explicit"
    actor: str | None = None
    input: str | None = None
    reply: str | None = None
    idempotency_key: str | None = None
    autonomous: bool = False


@runtime_checkable
class MemoryService(Protocol):
    """One notebook-addressed memory. ``manages`` services support edits and history."""

    manages: bool

    async def recall(
        self, notebook_id: str, context: RecallContext | None = None
    ) -> list[Candidate]: ...

    async def capture(
        self,
        notebook_id: str,
        facts: Sequence[str],
        *,
        author: str | None = None,
        context: CaptureContext | None = None,
    ) -> int: ...

    async def query(
        self, notebook_id: str, q: str, limit: int = 20, context: RecallContext | None = None
    ) -> list[str]: ...

    async def read(self, notebook_id: str) -> str: ...

    async def facts(self, notebook_id: str) -> list[Fact]: ...

    async def edit(
        self,
        notebook_id: str,
        edits: Sequence[Edit],
        *,
        author: str | None = None,
        context: CaptureMetadata | None = None,
    ) -> None: ...

    async def delete(
        self, notebook_id: str, ids: Sequence[str], *, author: str | None = None
    ) -> None: ...

    async def clear(self, notebook_id: str, *, author: str | None = None) -> int: ...

    async def history(self, notebook_id: str) -> list[Revision]: ...

    async def facts_at(self, notebook_id: str, revision: int) -> list[Fact]: ...

    async def restore(
        self, notebook_id: str, revision: int, *, author: str | None = None
    ) -> None: ...


class NotebookMemory:
    """Built-in notebook memory: stable-id facts, deduplicated capture, full history.

    Stored in the memory service; each operation is one call there (see
    ``supervisor.memory.client``), so its read and dependent write stay together.
    """

    manages = True

    def __init__(self, store: NotebookClient | UnavailableNotebooks) -> None:
        self._store = store

    @property
    def store(self) -> NotebookClient | UnavailableNotebooks:
        return self._store

    async def recall(
        self, notebook_id: str, context: RecallContext | None = None
    ) -> list[Candidate]:
        return recent_candidates(await self._store.facts(notebook_id))

    async def capture(
        self,
        notebook_id: str,
        facts: Sequence[str],
        *,
        author: str | None = None,
        context: CaptureContext | None = None,
    ) -> int:
        if not clean_captured(facts):
            return 0
        return await self._store.capture(notebook_id, facts, author=author, context=context)

    async def query(
        self, notebook_id: str, q: str, limit: int = 20, context: RecallContext | None = None
    ) -> list[str]:
        return query_facts(await self._store.facts(notebook_id), q, limit)

    async def read(self, notebook_id: str) -> str:
        return render_facts(await self._store.facts(notebook_id))

    async def facts(self, notebook_id: str) -> list[Fact]:
        return await self._store.facts(notebook_id)

    async def edit(
        self,
        notebook_id: str,
        edits: Sequence[Edit],
        *,
        author: str | None = None,
        context: CaptureMetadata | None = None,
    ) -> None:
        await self._store.edit(notebook_id, edits, author=author, context=context)

    async def delete(
        self, notebook_id: str, ids: Sequence[str], *, author: str | None = None
    ) -> None:
        await self._store.delete(notebook_id, ids, author=author)

    async def clear(self, notebook_id: str, *, author: str | None = None) -> int:
        """Remove every current fact; history keeps them (clearing is not erasure)."""
        return await self._store.clear(notebook_id, author=author)

    async def history(self, notebook_id: str) -> list[Revision]:
        return await self._store.revisions(notebook_id, limit=100_000)

    async def facts_at(self, notebook_id: str, revision: int) -> list[Fact]:
        return await self._store.facts_at(notebook_id, revision)

    async def restore(self, notebook_id: str, revision: int, *, author: str | None = None) -> None:
        """Make the notebook's facts what they were at ``revision`` (as new revisions)."""
        await self._store.restore(notebook_id, revision, author=author)
