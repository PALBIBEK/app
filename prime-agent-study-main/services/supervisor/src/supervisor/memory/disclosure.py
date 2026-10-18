"""Who may see which fact: one disclosure view for recall, search, tools and the API.

Ported from QM's memory/disclosure. The rule: the actor must still have access to
every source a fact was learned from, and every member of the audience the agent
is speaking to must be entitled to those sources too. Unknown provenance stays
within the notebook's own (narrow) audience. Lookups fail closed, and decisions
are never cached across reads (only within one read, so a revoked membership is
honoured on the next one).

Sources are ``personal:<principal>`` (entitled: that principal) and
``scope:<scope_id>`` (entitled: current members of that workspace scope).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field

from supervisor.memory.errors import MemoryDisclosureDenied, MemoryFactsMissing
from supervisor.memory.render import parse_fact_id, query_facts, render_facts
from supervisor.memory.service import MemoryService
from supervisor.memory.types import (
    Candidate,
    CaptureMetadata,
    Edit,
    Fact,
    Provenance,
    RecallContext,
    Revision,
    Sensitivity,
    personal_source,
)
from supervisor.memory.view import MemoryView

_log = logging.getLogger(__name__)

type IsMember = Callable[[str, str], Awaitable[bool]]


@dataclass(frozen=True, slots=True)
class Audience:
    """The disclosure context of one conversation.

    ``actor`` acts (and owns the notebooks); ``target`` is the conversation's own
    source; ``members`` are everyone who will read the agent's replies. ``open``
    enables QM's one relaxation: an *ordinary* personal fact may follow its owner
    into a conversation whose members are all internal principals.
    """

    actor: str
    target: str
    members: tuple[str, ...]
    is_member: IsMember | None = field(default=None, compare=False)
    open: bool = False
    internal: Callable[[str], bool] = field(default=lambda _p: True, compare=False)

    @classmethod
    def private(cls, actor: str) -> Audience:
        return cls(actor=actor, target=personal_source(actor), members=(actor,))


class _Policy:
    """Decisions for one read; memoised only for that read."""

    def __init__(self, audience: Audience) -> None:
        self._a = audience
        self._memo: dict[tuple[str, ...], asyncio.Future[bool]] = {}

    async def _once(self, key: tuple[str, ...], decide: Callable[[], Awaitable[bool]]) -> bool:
        if (pending := self._memo.get(key)) is None:
            pending = asyncio.ensure_future(self._safe(key, decide))
            self._memo[key] = pending
        return await pending

    @staticmethod
    async def _safe(key: tuple[str, ...], decide: Callable[[], Awaitable[bool]]) -> bool:
        try:
            return await decide()
        except Exception:  # noqa: BLE001 - disclosure fails closed on any lookup error
            _log.warning("memory disclosure lookup failed %s", key, exc_info=True)
            return False

    def entitled(self, person: str, source: str) -> Awaitable[bool]:
        async def decide() -> bool:
            kind, _, ref = source.partition(":")
            if kind == "personal":
                return person == ref
            if kind == "scope" and self._a.is_member is not None:
                return await self._a.is_member(person, ref)
            return False

        return self._once(("member", person, source), decide)

    async def all_entitled(self, source: str) -> bool:
        members = self._a.members
        return bool(members) and all(
            await asyncio.gather(*(self.entitled(m, source) for m in members))
        )

    async def narrow(self, home: str) -> bool:
        """Is the notebook's home audience the same as (or wider than) this audience?"""
        return await self.entitled(self._a.actor, home) and await self.all_entitled(home)

    def source_allowed(self, source: str, sensitivity: Sensitivity) -> Awaitable[bool]:
        async def decide() -> bool:
            a = self._a
            if not await self.entitled(a.actor, source):
                return False
            if await self.all_entitled(source):
                return True
            # QM's open-posture relaxation: only ordinary facts, only the actor's own,
            # only into an all-internal audience.
            return (
                a.open
                and sensitivity is Sensitivity.ORDINARY
                and source == personal_source(a.actor)
                and bool(a.members)
                and all(a.internal(m) for m in a.members)
            )

        return self._once(("source", source, sensitivity.value), decide)

    async def allows(self, home: str, record: Provenance) -> bool:
        async def home_allowed() -> bool:
            return await self.narrow(home) or await self.source_allowed(home, Sensitivity.UNKNOWN)

        if (record.source_unknown or record.sensitivity is Sensitivity.UNKNOWN) and not (
            await home_allowed()
        ):
            return False
        if not record.sources:
            return record.source_unknown and await home_allowed()
        return all(
            await asyncio.gather(
                *(self.source_allowed(s, record.sensitivity) for s in record.sources)
            )
        )


class DisclosedMemory(MemoryView):
    """A view of ``base`` that shows only what ``audience`` may see.

    Capture is not filtered (writing never discloses). Editing requires a narrow
    notebook and only touches visible facts; a partially visible notebook cannot be
    cleared or restored, because that would rewrite facts this audience cannot see.
    """

    def __init__(
        self, base: MemoryService, audience: Audience, home_of: Callable[[str], Awaitable[str]]
    ) -> None:
        super().__init__(base)
        self._audience = audience
        self._home_of = home_of

    def _policy(self) -> _Policy:
        return _Policy(self._audience)

    async def _visible(self, notebook_id: str, facts: Sequence[Fact] | None = None) -> list[Fact]:
        facts = await self._base.facts(notebook_id) if facts is None else facts
        home, policy = await self._home_of(notebook_id), self._policy()
        allowed = await asyncio.gather(*(policy.allows(home, f.provenance) for f in facts))
        return [f for f, ok in zip(facts, allowed, strict=True) if ok]

    async def _require_editable(self, notebook_id: str, ids: Sequence[str] = ()) -> None:
        if not await self._policy().narrow(await self._home_of(notebook_id)):
            raise MemoryDisclosureDenied
        shown = {f.id for f in await self._visible(notebook_id)}
        if hidden := [i for i in dict.fromkeys(parse_fact_id(x) for x in ids) if i not in shown]:
            raise MemoryFactsMissing(hidden)

    async def _require_fully_visible(self, notebook_id: str) -> None:
        await self._require_editable(notebook_id)
        facts = await self._base.facts(notebook_id)
        if len(await self._visible(notebook_id, facts)) != len(facts):
            raise MemoryDisclosureDenied

    async def recall(
        self, notebook_id: str, context: RecallContext | None = None
    ) -> list[Candidate]:
        shown = {f.id for f in await self._visible(notebook_id)}
        rows = await self._base.recall(notebook_id, context)
        if not self._base.manages:  # opaque provider: only within a narrow notebook
            narrow = await self._policy().narrow(await self._home_of(notebook_id))
            return rows if narrow else []
        return [r for r in rows if r.id is not None and r.id in shown]

    async def query(
        self, notebook_id: str, q: str, limit: int = 20, context: RecallContext | None = None
    ) -> list[str]:
        if not self._base.manages:
            narrow = await self._policy().narrow(await self._home_of(notebook_id))
            return await self._base.query(notebook_id, q, limit, context) if narrow else []
        return query_facts(await self._visible(notebook_id), q, limit)

    async def read(self, notebook_id: str) -> str:
        return render_facts(await self._visible(notebook_id))

    async def facts(self, notebook_id: str) -> list[Fact]:
        return await self._visible(notebook_id)

    async def edit(
        self,
        notebook_id: str,
        edits: Sequence[Edit],
        *,
        author: str | None = None,
        context: CaptureMetadata | None = None,
    ) -> None:
        await self._require_editable(notebook_id, [e.id for e in edits])
        await self._base.edit(notebook_id, edits, author=author, context=context)

    async def delete(
        self, notebook_id: str, ids: Sequence[str], *, author: str | None = None
    ) -> None:
        await self._require_editable(notebook_id, ids)
        await self._base.delete(notebook_id, ids, author=author)

    async def clear(self, notebook_id: str, *, author: str | None = None) -> int:
        await self._require_fully_visible(notebook_id)
        return await self._base.clear(notebook_id, author=author)

    async def history(self, notebook_id: str) -> list[Revision]:
        await self._require_fully_visible(notebook_id)
        return await self._base.history(notebook_id)

    async def facts_at(self, notebook_id: str, revision: int) -> list[Fact]:
        # Historical facts are floored by newer revisions, then filtered like current ones.
        return await self._visible(notebook_id, await self._base.facts_at(notebook_id, revision))

    async def restore(self, notebook_id: str, revision: int, *, author: str | None = None) -> None:
        await self._require_fully_visible(notebook_id)
        historical = await self._base.facts_at(notebook_id, revision)
        if len(await self._visible(notebook_id, historical)) != len(historical):
            raise MemoryDisclosureDenied  # the complete intervening history must be visible
        await self._base.restore(notebook_id, revision, author=author)
