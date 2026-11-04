"""Erase an ended session: everything the supervisor keeps about it, gone for good.

Ending a session (``DELETE /v1/sessions/{id}``) stops it and keeps its record. Erasing
it (``DELETE /v1/sessions/{id}/data``) removes that record too:

- its journal (every message, call and result) and the large payloads it kept beside
  it, unless another session's journal holds the same bytes (they are stored once);
- its runs' inputs, traces and scores, approvals, commands, tool outbox, partial reply;
- the files its runs produced (artifacts), and its sandboxes' checkpoints;
- its gateway key, its hands' leases, and the session itself.

What stays is a tombstone (``erased_sessions``: its id, tenant and when), so asking for the
session afterwards answers 410 Gone instead of 404, and clients can tell an erased session
from one that is not set up yet.

Not here: the workspace files it attached (they are the workspace's, not the
session's), what long-term memory learnt from it (the principal's; erased with the
account), and its workflow history, which Temporal keeps sealed until the namespace's
retention ends.

Database rows go in one transaction; stored bytes (artifacts, checkpoints, payloads) are
deleted after it commits, each best effort: an object left behind is unreachable, and
erasing again finds nothing left to remove.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from sqlalchemy import Table, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.db.models import (
    ApprovalRow,
    ArtifactRow,
    CommandRow,
    ErasedSessionRow,
    EventRow,
    HandLeaseRow,
    JournalBlobRefRow,
    PartialOutputRow,
    RunInputRow,
    RunTraceRow,
    ScopeGrantRow,
    ScopeMembershipRow,
    ScopeRow,
    SessionAttachmentRow,
    SessionIndexRow,
    SessionRow,
    ToolOutboxRow,
    TraceScoreRow,
    VaultSecretRow,
    WorkspaceFileRow,
    WorkspaceFileVersionRow,
)
from supervisor.hands.checkpoint import checkpoint_scope
from supervisor.hands.keys import DEFAULT_HAND, hand_key, split_key
from supervisor.journal import blobrefs
from supervisor.journal.blobs import BlobStore, LocalBlobs
from supervisor.seams.services.object_storage import ObjectStorage

__all__ = ["Erased", "SessionEraser"]

logger = logging.getLogger(__name__)

_HAND_EVENTS = ("HandProvisioned", "HandCheckpointed", "HandStopped", "HandLost")

_SESSION_TABLES: Final[tuple[Table, ...]] = tuple(
    row.__table__  # type: ignore[misc]
    for row in (
        PartialOutputRow,
        ToolOutboxRow,
        ApprovalRow,
        CommandRow,
        ArtifactRow,
        SessionAttachmentRow,
        RunInputRow,
        RunTraceRow,
        TraceScoreRow,
        SessionIndexRow,
        JournalBlobRefRow,
        EventRow,
    )
)
"""Rows keyed by the session, in the order they go (before the session's own row)."""

_SCOPE_TABLES: Final[tuple[Table, ...]] = tuple(
    row.__table__  # type: ignore[misc]
    for row in (WorkspaceFileVersionRow, WorkspaceFileRow, ScopeGrantRow, ScopeMembershipRow)
)
"""Rows of the session's checkpoint scopes, before the scopes."""


@dataclass(frozen=True, slots=True)
class Erased:
    """What erasing removed (zero everywhere when the session was already erased)."""

    events: int = 0
    artifacts: int = 0
    checkpoint_files: int = 0
    payloads: int = 0


class SessionEraser:
    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        *,
        blobs: BlobStore | None = None,
        blob_dir: Path | None = None,
        objects: ObjectStorage | None,
    ) -> None:
        """``blobs``: where the journal's large payloads are (``supervisor.journal.blobs``);
        ``blob_dir``: a directory instead."""
        self._db = factory
        self._blobs: BlobStore | None = blobs or (LocalBlobs(blob_dir) if blob_dir is not None else None)
        self._objects = objects

    async def erase(self, session_id: str) -> Erased:
        """Erase ``session_id``'s data, and its subagents' (``supervisor.session.children``:
        sessions it started, which end with it). The caller ends the session first: a
        running one would go on writing what this removes."""
        async with self._db() as db:
            children = list(await db.scalars(select(SessionRow.id).where(SessionRow.parent_session_id == session_id)))
        erased = [await self.erase(child) for child in children]
        own = await self._erase_one(session_id)
        return Erased(
            events=own.events + sum(e.events for e in erased),
            artifacts=own.artifacts + sum(e.artifacts for e in erased),
            checkpoint_files=own.checkpoint_files + sum(e.checkpoint_files for e in erased),
            payloads=own.payloads + sum(e.payloads for e in erased),
        )

    async def _erase_one(self, session_id: str) -> Erased:
        async with self._db() as db, db.begin():
            refs = await self._payload_refs(db, session_id)
            scopes = [checkpoint_scope(key) for key in await self._hand_keys(db, session_id)]
            artifact_keys = list(await db.scalars(select(ArtifactRow.object_key).where(ArtifactRow.session_id == session_id)))
            checkpoint_keys = await self._checkpoint_objects(db, scopes)
            events = await self._delete_rows(db, session_id, scopes)
        for key in [*artifact_keys, *checkpoint_keys]:
            await self._delete_object(key)
        payloads = await self._delete_unreferenced(refs)
        return Erased(events=events, artifacts=len(artifact_keys), checkpoint_files=len(checkpoint_keys), payloads=payloads)

    @staticmethod
    async def _payload_refs(db: AsyncSession, session_id: str) -> set[str]:
        """Payloads kept beside the journal: large events', and lost hands' logs."""
        return set(await db.scalars(select(JournalBlobRefRow.ref).where(JournalBlobRefRow.session_id == session_id)))

    @staticmethod
    async def _hand_keys(db: AsyncSession, session_id: str) -> set[str]:
        """The session's hands (its default one, and every named one its journal shows)."""
        payloads = await db.scalars(
            select(EventRow.payload).where(EventRow.session_id == session_id, EventRow.type.in_(_HAND_EVENTS))
        )
        names = {str((p or {}).get("hand") or DEFAULT_HAND) for p in payloads}
        return {hand_key(session_id, name) for name in {DEFAULT_HAND, *names}}

    @staticmethod
    async def _checkpoint_objects(db: AsyncSession, scopes: Sequence[str]) -> list[str]:
        versions = await db.scalars(
            select(WorkspaceFileVersionRow.object_key).where(WorkspaceFileVersionRow.scope_id.in_(scopes))
        )
        current = await db.scalars(select(WorkspaceFileRow.object_key).where(WorkspaceFileRow.scope_id.in_(scopes)))
        return sorted({key for key in [*versions, *current] if key})

    @staticmethod
    async def _delete_rows(db: AsyncSession, session_id: str, scopes: Sequence[str]) -> int:
        """Every row of the session, children before what they point at; the events removed."""
        events = await db.scalar(select(func.count()).select_from(EventRow).where(EventRow.session_id == session_id))
        for table in _SESSION_TABLES:
            await db.execute(delete(table).where(table.c.session_id == session_id))
        await db.execute(
            delete(VaultSecretRow).where(VaultSecretRow.owner_kind == "session", VaultSecretRow.owner_id == session_id)
        )
        leases = await db.scalars(select(HandLeaseRow.hand_key).where(HandLeaseRow.hand_key.startswith(session_id)))
        mine = [key for key in leases if split_key(key)[0] == session_id]
        await db.execute(delete(HandLeaseRow).where(HandLeaseRow.hand_key.in_(mine)))
        for table in _SCOPE_TABLES:
            await db.execute(delete(table).where(table.c.scope_id.in_(scopes)))
        await db.execute(delete(ScopeRow).where(ScopeRow.id.in_(scopes)))
        tenant_id = await db.scalar(select(SessionRow.tenant_id).where(SessionRow.id == session_id))
        if tenant_id is not None:  # remembered as erased: asking for it says so (410)
            await db.merge(ErasedSessionRow(session_id=session_id, tenant_id=tenant_id))
        await db.execute(delete(SessionRow).where(SessionRow.id == session_id))
        return int(events or 0)

    async def _delete_object(self, key: str) -> None:
        if self._objects is None:
            return
        try:
            await self._objects.delete(key)
        except Exception:
            logger.warning("could not delete object %s while erasing a session", key, exc_info=True)

    async def _delete_unreferenced(self, refs: set[str]) -> int:
        """Delete the payloads no other session's journal still names (they are stored once):
        one indexed lookup (``supervisor.journal.blobrefs``), not a scan of every journal."""
        if self._blobs is None or not refs:
            return 0
        try:
            return await blobrefs.forget_unused(self._db, self._blobs, refs)
        except Exception:
            logger.warning("could not delete journal payloads while erasing", exc_info=True)
            return 0

