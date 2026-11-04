"""Append-only journal writer. Allocates seq — never trusts the runtime."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Coroutine, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.agent.events import AgentEvent, RunEnd
from supervisor.db.models import EventRow, SessionIndexRow, SessionRow
from supervisor.journal import index, snapshots
from supervisor.journal.blobs import BlobStore, LocalBlobs, blob_ref
from supervisor.journal.reader import JournalReader
from supervisor.journal.secrets import mask
from supervisor.journal.serde import encode_event, event_type_name


def keyed_event_id(session_id: str, event_key: str) -> str:
    """Deterministic event id (fits EventRow.event_id) for an idempotent append."""
    return uuid5(NAMESPACE_URL, f"yal-journal:{session_id}:{event_key}").hex

# Default: offload payloads larger than 64 KiB.
_DEFAULT_BLOB_THRESHOLD = 64 * 1024
_COPY_BATCH = 500  # events a copy writes per transaction, at most


class OversizedPayloadError(RuntimeError):
    """Payload exceeds threshold but no blob_dir is configured."""


@dataclass(frozen=True, slots=True)
class Written:
    """Where an event is in the journal: as stored (its id and time are the row's), and
    whether this write put it there or found it there already (a retried keyed write)."""

    seq: int
    event_id: str
    ts_ms: int
    created: bool


class JournalWriter:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        blob_dir: Path | str | None = None,
        blobs: BlobStore | None = None,
        blob_threshold: int = _DEFAULT_BLOB_THRESHOLD,
    ) -> None:
        """``blobs`` keeps what is too large for a row (``supervisor.journal.blobs``: shared
        object storage when more than one process reads the journal); ``blob_dir`` is a
        directory for it instead (one process, dev, tests)."""
        self._factory = session_factory
        self._blobs: BlobStore | None = blobs or (LocalBlobs(blob_dir) if blob_dir is not None else None)
        self._blob_threshold = blob_threshold
        self._reader = JournalReader(session_factory, blobs=self._blobs)  # a RunEnd's base

    @property
    def blobs(self) -> BlobStore | None:
        return self._blobs

    async def ensure_session(
        self,
        session_id: str,
        *,
        runtime: str = "echo",
        tenant_id: str = "default",
        harness_version: str = "",
    ) -> None:
        """Create the session's row and index once; safe to race (a retried create, a
        retried subagent copy): whoever loses finds the row there and returns."""
        async with self._factory() as db:
            if await db.get(SessionRow, session_id) is not None:
                return
            try:
                db.add(
                    SessionRow(
                        id=session_id,
                        tenant_id=tenant_id,
                        runtime=runtime,
                        harness_version=harness_version,
                        status="active",
                        last_seq=-1,
                    )
                )
                # Flush parent first — no ORM relationship, so commit order is not guaranteed.
                await db.flush()
                db.add(
                    SessionIndexRow(
                        session_id=session_id,
                        title=None,
                        message_count=0,
                        total_cost=0,
                        last_event_at=None,
                    )
                )
                await db.commit()
            except IntegrityError:
                await db.rollback()
                if await db.get(SessionRow, session_id) is None:
                    raise  # not the race: something else is wrong

    async def append(
        self,
        session_id: str,
        event: AgentEvent,
        *,
        turn_id: str | None,
        trace_id: str | None,
        event_key: str | None = None,
    ) -> int:
        """Allocate seq, commit the row, return seq. Durable before return.

        With ``event_key`` the append is idempotent: the event id is derived from
        the key, and appending the same key again returns the original seq. This
        is what lets a retried workflow activity journal safely.
        """
        return (await self.write(session_id, event, turn_id=turn_id, trace_id=trace_id, event_key=event_key)).seq

    async def write(
        self,
        session_id: str,
        event: AgentEvent,
        *,
        turn_id: str | None,
        trace_id: str | None,
        event_key: str | None = None,
    ) -> Written:
        """``append``, saying where the event is as stored, and whether this write put it
        there. A keyed write is idempotent even when two attempts race (Temporal can start a
        step's next attempt while the last one still runs): the key is looked up under the
        session's lock, so of two writers exactly one appends and the other gets its event."""
        if event_key is None:
            return await _run_to_completion(self._write(session_id, event, turn_id, trace_id, None))
        event_id = keyed_event_id(session_id, event_key)
        if (found := await self._find(session_id, event_id)) is not None:
            return found  # the usual retry: no lock taken
        try:
            return await _run_to_completion(self._write(session_id, event, turn_id, trace_id, event_id))
        except _AlreadyAppended:
            found = await self._find(session_id, event_id)
            assert found is not None
            return found

    async def _write(
        self, session_id: str, event: AgentEvent, turn_id: str | None, trace_id: str | None, event_id: str | None
    ) -> Written:
        keyed, stored_id = event_id is not None, event_id or uuid4().hex
        seq, ts_ms = await self._append_batch(
            session_id, [(event, turn_id, trace_id)], event_ids=[stored_id], if_absent=keyed
        )
        return Written(seq, stored_id, ts_ms, created=True)

    async def _find(self, session_id: str, event_id: str) -> Written | None:
        async with self._factory() as db:
            row = (
                await db.execute(
                    select(EventRow.seq, EventRow.ts_ms).where(
                        EventRow.session_id == session_id, EventRow.event_id == event_id
                    )
                )
            ).first()
        return None if row is None else Written(int(row.seq), event_id, int(row.ts_ms), created=False)

    async def append_once(
        self,
        session_id: str,
        event: AgentEvent,
        *,
        turn_id: str | None,
        trace_id: str | None,
        event_key: str,
    ) -> Written | None:
        """Append under ``event_key`` only if nothing holds that key yet: where it was
        written, or None when another writer already did. Atomic: the check runs inside the append's
        transaction, after the session row's lock, so of two writers racing for one key
        exactly one wins (a claim: who may run a tool call, who closes it unrun).
        """
        event_id = keyed_event_id(session_id, event_key)
        if await self.seq_of(session_id, event_id) is not None:
            return None
        try:
            seq, ts_ms = await _run_to_completion(
                self._append_batch(
                    session_id, [(event, turn_id, trace_id)], event_ids=[event_id], if_absent=True
                )
            )
        except _AlreadyAppended:
            return None
        return Written(seq, event_id, ts_ms, created=True)

    async def seq_of(self, session_id: str, event_id: str) -> int | None:
        async with self._factory() as db:
            seq = (
                await db.execute(
                    select(EventRow.seq).where(
                        EventRow.session_id == session_id, EventRow.event_id == event_id
                    )
                )
            ).scalar_one_or_none()
        return None if seq is None else int(seq)

    async def append_missing(
        self,
        session_id: str,
        events: Sequence[tuple[AgentEvent, str | None, str | None, str]],
        *,
        batch: int = _COPY_BATCH,
    ) -> int:
        """Append each of ``events`` (event, turn id, trace id, event key) not in the
        journal yet, in order, a batch per transaction; how many it appended.

        For copying a journal (a fork): retried, or racing another attempt, it carries on
        where the journal is, and never appends a key twice. A batch ends after a
        ``RunEnd``, so each stores as what it added to the one before
        (``supervisor.journal.snapshots``)."""
        keyed = [(event, turn, trace, keyed_event_id(session_id, key)) for event, turn, trace, key in events]
        appended = 0
        for _ in range(len(keyed) + 1):  # each round appends at least one batch or ends
            present = await self._present(session_id, [k for *_, k in keyed])
            todo = [e for e in keyed if e[3] not in present]
            if not todo:
                return appended
            try:
                for chunk in _batches(todo, batch):
                    await _run_to_completion(
                        self._append_batch(
                            session_id, [(e, t, tr) for e, t, tr, _ in chunk], event_ids=[k for *_, k in chunk], if_absent=True
                        )
                    )
                    appended += len(chunk)
            except _AlreadyAppended:
                continue  # another attempt wrote some of them: see which are left
        raise RuntimeError(f"session {session_id}: copying made no progress")

    async def _present(self, session_id: str, event_ids: list[str]) -> set[str]:
        found: set[str] = set()
        async with self._factory() as db:
            for i in range(0, len(event_ids), 500):
                rows = await db.execute(
                    select(EventRow.event_id).where(
                        EventRow.session_id == session_id, EventRow.event_id.in_(event_ids[i : i + 500])
                    )
                )
                found.update(rows.scalars())
        return found

    async def append_batch(
        self,
        session_id: str,
        events: list[tuple[AgentEvent, str | None, str | None]],
        *,
        event_ids: list[str | None] | None = None,
    ) -> int:
        """Atomically append n events. Returns the last allocated seq.

        The write runs to completion even if the caller is cancelled (a session
        terminating cancels its event pump): abandoned mid-transaction, the
        connection would keep the database's write lock until it was collected,
        and on SQLite every other writer would time out behind it.
        """
        if not events:
            raise ValueError("append_batch requires at least one event")
        keyed = any(event_ids or [])
        try:
            seq, _ = await _run_to_completion(
                self._append_batch(session_id, events, event_ids=event_ids, if_absent=keyed)
            )
        except _AlreadyAppended:
            # A retry of the same batch: every event of it is there (it was one transaction).
            last = next(e for e in reversed(event_ids or []) if e is not None)
            seq_found = await self.seq_of(session_id, last)
            assert seq_found is not None
            return seq_found
        return seq

    async def _append_batch(
        self,
        session_id: str,
        events: list[tuple[AgentEvent, str | None, str | None]],
        *,
        event_ids: list[str | None] | None,
        if_absent: bool = False,
    ) -> tuple[int, int]:
        """Append ``events`` in one transaction under the session row's lock; (the last
        seq, the time stamped on them). ``if_absent``: raise ``_AlreadyAppended`` (rolling
        back) when an event id given is already in the journal; checked under the lock, so a
        racing writer that got there first has committed and is seen."""
        n = len(events)
        # Large payloads go to the blob store first, outside the session's lock (they are
        # content-addressed: one left by a write that then fails is harmless, and erasing
        # deletes what no journal names).
        payloads = [await self._store_payload(session_id, event) for event, _, _ in events]

        async with self._factory() as db, db.begin():
            # Serialize allocators under SQLite.
            await db.execute(
                text("SELECT id FROM sessions WHERE id = :id"),
                {"id": session_id},
            )
            result = await db.execute(
                text(
                    "UPDATE sessions SET last_seq = last_seq + :n, updated_at = :now "
                    "WHERE id = :id RETURNING last_seq"
                ),
                {
                    "n": n,
                    "id": session_id,
                    "now": datetime.now(UTC),
                },
            )
            last_seq = result.scalar_one()
            if if_absent and (keys := [e for e in event_ids or [] if e is not None]):
                # Under the session row's lock: a writer that got here first has committed.
                taken = await db.execute(
                    select(EventRow.seq)
                    .where(EventRow.session_id == session_id, EventRow.event_id.in_(keys))
                    .limit(1)
                )
                if taken.scalar_one_or_none() is not None:
                    raise _AlreadyAppended  # rolls the seq allocation back
            first = int(last_seq) - n + 1
            now_ms = int(time.time() * 1000)
            in_chat, title, active = 0, None, False
            for i, (event, turn_id, trace_id) in enumerate(events):
                seq = first + i
                payload = payloads[i]
                db.add(
                    EventRow(
                        session_id=session_id,
                        seq=seq,
                        event_id=(event_ids[i] if event_ids else None) or uuid4().hex,
                        turn_id=turn_id,
                        type=event_type_name(event),
                        payload=payload,
                        ts_ms=now_ms,
                        trace_id=trace_id,
                    )
                )
                if index.in_chat(event):
                    in_chat += 1
                    title = title or index.title_of(event)
                active = active or index.active(event)
            if active:
                await self._touch_index(db, session_id, messages=in_chat, title=title, ts_ms=now_ms)
        return int(last_seq), now_ms

    async def append_in_session(
        self,
        db: AsyncSession,
        session_id: str,
        event: AgentEvent,
        *,
        turn_id: str | None,
        trace_id: str | None,
    ) -> Written:
        """Append one event inside an open transaction (caller owns begin/commit); where it
        is once that commits."""
        await db.execute(
            text("SELECT id FROM sessions WHERE id = :id"),
            {"id": session_id},
        )
        result = await db.execute(
            text(
                "UPDATE sessions SET last_seq = last_seq + 1, updated_at = :now "
                "WHERE id = :id RETURNING last_seq"
            ),
            {
                "id": session_id,
                "now": datetime.now(UTC),
            },
        )
        seq = int(result.scalar_one())
        now_ms = int(time.time() * 1000)
        payload = await self._store_payload(session_id, event)
        event_id = uuid4().hex
        db.add(
            EventRow(
                session_id=session_id,
                seq=seq,
                event_id=event_id,
                turn_id=turn_id,
                type=event_type_name(event),
                payload=payload,
                ts_ms=now_ms,
                trace_id=trace_id,
            )
        )
        return Written(seq, event_id, now_ms, created=True)

    async def _touch_index(
        self, db: AsyncSession, session_id: str, *, messages: int, title: str | None, ts_ms: int
    ) -> None:
        """The session's index after it did something, with ``messages`` more chat
        messages (``supervisor.journal.index``). Not its cost: the budget tracker owns ``total_cost``
        (it charges each call once, before it is journalled)."""
        idx = await db.get(SessionIndexRow, session_id)
        if idx is None:
            idx = SessionIndexRow(session_id=session_id, message_count=0)
            db.add(idx)
        idx.message_count = int(idx.message_count or 0) + messages
        if idx.title is None and title is not None:
            idx.title = title
        idx.last_event_at = datetime.fromtimestamp(ts_ms / 1000, tz=UTC)

    async def put_blob(self, data: bytes) -> str | None:
        """Keep ``data`` content-addressed beside the journal; its ref (SHA-256), ``None``
        without a blob store."""
        if self._blobs is None:
            return None
        ref = blob_ref(data)
        await self._blobs.put(ref, data)
        return ref

    async def _store_payload(self, session_id: str, event: AgentEvent) -> dict[str, Any]:
        encoded = mask(encode_event(event))  # no credential is kept (supervisor.journal.secrets)
        if isinstance(event, RunEnd):
            # What it adds to the session's previous RunEnd (supervisor.journal.snapshots).
            # Any committed RunEnd is a sound base, so a racing write costs only size.
            encoded = snapshots.encode_run_end(encoded, await self._reader.latest_run_end(session_id))
        raw = json.dumps(encoded, separators=(",", ":"), allow_nan=False).encode()  # plain JSON already
        if len(raw) <= self._blob_threshold:
            return encoded
        if self._blobs is None:
            raise OversizedPayloadError(
                f"payload {len(raw)} bytes exceeds threshold {self._blob_threshold} "
                "and no blob store configured"
            )
        ref = blob_ref(raw)
        await self._blobs.put(ref, raw)
        return {"_type": event_type_name(event), "_blob_ref": ref}


type _Keyed = tuple[AgentEvent, str | None, str | None, str]
"""An event to append with its turn id, trace id and event id."""


def _batches(items: Sequence[_Keyed], size: int) -> Iterator[list[_Keyed]]:
    """``items`` in runs of at most ``size``, each ending after a ``RunEnd`` it holds."""
    chunk: list[_Keyed] = []
    for item in items:
        chunk.append(item)
        if len(chunk) >= size or isinstance(item[0], RunEnd):
            yield chunk
            chunk = []
    if chunk:
        yield chunk


class _AlreadyAppended(Exception):
    """``append_once`` lost the race for its key."""


async def _run_to_completion[T](work: Coroutine[Any, Any, T]) -> T:
    """Await ``work``; if the caller is cancelled, ``work`` still finishes (and its error is logged)."""
    task = asyncio.ensure_future(work)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        task.add_done_callback(_log_failure)
        raise


def _log_failure(task: asyncio.Future[Any]) -> None:
    if not task.cancelled() and (exc := task.exception()) is not None and not isinstance(
        exc, _AlreadyAppended
    ):  # a lost claim is an answer, not a failure
        logging.getLogger(__name__).error("journal write failed after cancellation", exc_info=exc)
