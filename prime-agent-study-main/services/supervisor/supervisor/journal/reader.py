"""Journal reader — replay, unpaired ToolStart detection, latest history."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.agent.events import (
    CompactionEnd,
    CompactionStart,
    Envelope,
    JobEnd,
    JobStart,
    ToolEnd,
    ToolStart,
)
from supervisor.db.models import EventRow, SessionRow
from supervisor.journal import snapshots
from supervisor.journal.blobs import BlobStore, LocalBlobs
from supervisor.journal.secrets import mask_bytes
from supervisor.journal.serde import decode_event
from supervisor.journal.snapshots import MAX_CHAIN, Snapshot


class JournalReader:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        blob_dir: Path | str | None = None,
        blobs: BlobStore | None = None,
    ) -> None:
        self._factory = session_factory
        # Where large payloads are (supervisor.journal.blobs); the writer's store.
        self._blobs: BlobStore | None = blobs or (LocalBlobs(blob_dir) if blob_dir is not None else None)

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        return self._factory

    async def read(self, session_id: str, *, since: int = -1) -> list[Envelope]:
        """Return envelopes with seq > since (exclusive), in order."""
        async with self._factory() as db:
            rows = (
                await db.execute(
                    select(EventRow)
                    .where(EventRow.session_id == session_id, EventRow.seq > since)
                    .order_by(EventRow.seq.asc())
                )
            ).scalars().all()
        return await self._envelopes(rows)

    async def hand_logs(self, session_id: str, ref: str) -> bytes | None:
        """A lost hand's saved logs, if one of the session's ``HandLost`` events names ``ref``."""
        async with self._factory() as db:
            rows = (
                await db.execute(
                    select(EventRow).where(EventRow.session_id == session_id, EventRow.type == "HandLost")
                )
            ).scalars().all()
        if not any(getattr(e.payload, "logs_ref", None) == ref for e in await self._envelopes(rows)):
            return None
        try:
            return mask_bytes(await self.read_blob(ref))  # those saved before logs were masked too
        except FileNotFoundError:
            return None

    async def unpaired_tool_starts(self, session_id: str) -> list[ToolStart]:
        """ToolStart events whose tool_call_id has no matching ToolEnd."""
        envs = await self.read(session_id, since=-1)
        ended: set[str] = set()
        starts: dict[str, ToolStart] = {}
        for env in envs:
            if isinstance(env.payload, ToolStart):
                starts[env.payload.tool_call_id] = env.payload
            elif isinstance(env.payload, ToolEnd):
                ended.add(env.payload.tool_call_id)
        return [starts[cid] for cid in starts if cid not in ended]

    async def unpaired_compaction_starts(self, session_id: str) -> list[CompactionStart]:
        """CompactionStart events whose compaction_id has no matching CompactionEnd."""
        ended: set[str] = set()
        starts: dict[str, CompactionStart] = {}
        for env in await self.read(session_id, since=-1):
            if isinstance(env.payload, CompactionStart):
                starts[env.payload.compaction_id] = env.payload
            elif isinstance(env.payload, CompactionEnd):
                ended.add(env.payload.compaction_id)
        return [starts[cid] for cid in starts if cid not in ended]

    async def last_seq(self, session_id: str) -> int:
        async with self._factory() as db:
            row = await db.get(SessionRow, session_id)
            if row is None:
                return -1
            return int(row.last_seq)

    async def open_run(self, session_id: str) -> tuple[int, str | None] | None:
        """(seq, turn_id) of a RunStart whose run never finished (crashed), else None.

        A run ends with RunEnd, an ErrorEvent, or an aborted TurnEnd.
        """
        async with self._factory() as db:
            boundary = (
                await db.execute(
                    select(EventRow)
                    .where(
                        EventRow.session_id == session_id,
                        EventRow.type.in_(("RunStart", "RunEnd", "ErrorEvent")),
                    )
                    .order_by(EventRow.seq.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if boundary is None or boundary.type != "RunStart":
                return None
            run_start = (int(boundary.seq), boundary.turn_id)
            turn_ends = (
                await db.execute(
                    select(EventRow).where(
                        EventRow.session_id == session_id,
                        EventRow.type == "TurnEnd",
                        EventRow.seq > boundary.seq,
                    )
                )
            ).scalars().all()
        if any(getattr(e.payload, "stop_reason", None) == "aborted" for e in await self._envelopes(turn_ends)):
            return None
        return run_start

    async def read_turn(self, session_id: str, turn_id: str) -> list[Envelope]:
        """One run's events, in order (workflow-driven runs tag every event with it)."""
        async with self._factory() as db:
            rows = (
                await db.execute(
                    select(EventRow)
                    .where(EventRow.session_id == session_id, EventRow.turn_id == turn_id)
                    .order_by(EventRow.seq.asc())
                )
            ).scalars().all()
        return await self._envelopes(rows)

    async def read_run_window(self, session_id: str, turn_id: str) -> list[Envelope]:
        """One run's events plus the session's own events (those with no run) that happened
        while it ran: a sandbox starting or being saved belongs to the run it served."""
        async with self._factory() as db:
            bounds = (
                await db.execute(
                    select(func.min(EventRow.seq), func.max(EventRow.seq)).where(
                        EventRow.session_id == session_id, EventRow.turn_id == turn_id
                    )
                )
            ).one()
            if bounds[0] is None:
                return []
            first, last = bounds
            ended = (
                await db.execute(
                    select(EventRow.seq).where(
                        EventRow.session_id == session_id, EventRow.turn_id == turn_id, EventRow.type == "RunEnd"
                    )
                )
            ).first()
            in_window = EventRow.seq >= first
            if ended is not None:
                in_window = in_window & (EventRow.seq <= last)
            rows = (
                await db.execute(
                    select(EventRow)
                    .where(
                        EventRow.session_id == session_id,
                        in_window,
                        (EventRow.turn_id == turn_id) | EventRow.turn_id.is_(None),
                    )
                    .order_by(EventRow.seq.asc())
                )
            ).scalars().all()
        return await self._envelopes(rows)

    async def event(self, session_id: str, event_id: str) -> Envelope | None:
        async with self._factory() as db:
            row = (
                await db.execute(
                    select(EventRow).where(
                        EventRow.session_id == session_id, EventRow.event_id == event_id
                    )
                )
            ).scalar_one_or_none()
        return None if row is None else (await self._envelopes([row]))[0]

    async def latest_history(self, session_id: str) -> list[dict] | None:
        """Messages from the most recent RunEnd, or None if none."""
        latest = await self.latest_run_end(session_id)
        return None if latest is None else list(latest.messages)

    async def latest_run_end(self, session_id: str) -> Snapshot | None:
        """The session's most recent ``RunEnd`` conversation, resolved (at most
        ``MAX_CHAIN`` + 1 rows read, however long the session)."""
        done = await self._snapshots(session_id, None)
        return done[max(done)] if done else None

    async def last_of(
        self, session_id: str, types: Sequence[str], *, limit: int = 1, before_seq: int | None = None
    ) -> list[Envelope]:
        """The session's latest ``limit`` events of ``types``, newest first, without
        reading the rest of its journal."""
        query = select(EventRow).where(EventRow.session_id == session_id, EventRow.type.in_(list(types)))
        if before_seq is not None:
            query = query.where(EventRow.seq < before_seq)
        async with self._factory() as db:
            rows = (await db.execute(query.order_by(EventRow.seq.desc()).limit(limit))).scalars().all()
        return await self._envelopes(rows)

    async def of_types(self, session_id: str, types: Sequence[str], *, since: int = -1) -> list[Envelope]:
        """The session's events of ``types`` after ``since``, in order: one indexed read of
        those rows, not the session's whole journal."""
        async with self._factory() as db:
            rows = (
                await db.execute(
                    select(EventRow)
                    .where(EventRow.session_id == session_id, EventRow.type.in_(list(types)), EventRow.seq > since)
                    .order_by(EventRow.seq.asc())
                )
            ).scalars().all()
        return await self._envelopes(rows)

    async def read_types(
        self,
        session_ids: list[str],
        types: list[str],
        *,
        since_ms: int | None = None,
        until_ms: int | None = None,
        limit: int = 200_000,
    ) -> list[Envelope]:
        """Events of ``types`` across ``session_ids`` (oldest first), optionally in a time window.

        For views over many sessions (sandboxes, usage): one query, filtered by the
        event-type column, instead of reading each session's whole journal.
        """
        if not session_ids or not types:
            return []
        query = select(EventRow).where(EventRow.session_id.in_(session_ids), EventRow.type.in_(types))
        if since_ms is not None:
            query = query.where(EventRow.ts_ms >= since_ms)
        if until_ms is not None:
            query = query.where(EventRow.ts_ms < until_ms)
        async with self._factory() as db:
            rows = (
                await db.execute(query.order_by(EventRow.ts_ms.asc(), EventRow.seq.asc()).limit(limit))
            ).scalars().all()
        return await self._envelopes(rows)

    async def _envelopes(self, rows: Sequence[EventRow]) -> list[Envelope]:
        """``rows`` as envelopes: large payloads fetched from the blob store at once, and
        ``RunEnd`` deltas (``supervisor.journal.snapshots``) resolved into conversations."""
        payloads = await self._resolve(list(zip(rows, await self._payloads(rows), strict=True)))
        return [
            Envelope(
                session_id=row.session_id,
                seq=int(row.seq),
                event_id=row.event_id,
                ts_ms=int(row.ts_ms),
                turn_id=row.turn_id,
                trace_id=row.trace_id,
                payload=decode_event(payload),
            )
            for row, payload in zip(rows, payloads, strict=True)
        ]

    async def _payloads(self, rows: Sequence[EventRow]) -> list[dict]:
        """Each row's payload, with those kept in the blob store fetched concurrently."""
        refs = {ref for row in rows if (ref := row.payload.get("_blob_ref")) is not None}
        fetched = dict(zip(refs, await asyncio.gather(*(self._blob(ref) for ref in refs)), strict=True))
        return [fetched.get(row.payload.get("_blob_ref")) or row.payload for row in rows]

    async def _resolve(self, items: list[tuple[EventRow, dict]]) -> list[dict]:
        """``items``' payloads with every ``RunEnd`` delta replaced by the ``RunEnd`` it
        stands for. Bases in ``items`` are used as they are; others are read (one query a
        chain: they are the session's preceding ``RunEnd`` rows)."""
        if not any(snapshots.is_delta(p) for _, p in items):
            return [p for _, p in items]
        known: dict[tuple[str, int], list[dict]] = {}
        out = [p for _, p in items]
        for i in sorted(range(len(items)), key=lambda i: (items[i][0].session_id, int(items[i][0].seq))):
            row, payload = items[i]
            if snapshots.is_delta(payload):
                key = (row.session_id, int(payload["_base_seq"]))
                if key not in known:
                    known.update({(row.session_id, n): s.messages for n, s in (await self._snapshots(*key)).items()})
                if key not in known:
                    raise LookupError(f"session {row.session_id}: RunEnd {row.seq} has no base {key[1]}")
                payload = snapshots.resolve(payload, known[key])
                out[i] = payload
            if row.type == "RunEnd":
                known[(row.session_id, int(row.seq))] = payload["messages"]
        return out

    async def _snapshots(self, session_id: str, through_seq: int | None) -> dict[int, Snapshot]:
        """The resolved ``RunEnd`` conversation at ``through_seq`` (the newest when
        ``None``), with those it was resolved from, by seq; empty if there is none.

        One query reads the ``MAX_CHAIN`` + 1 ``RunEnd`` rows up to it, which hold its
        whole chain when each delta stands on the ``RunEnd`` before it (the writer's
        normal case); a base outside them is read on its own."""
        query = select(EventRow).where(EventRow.session_id == session_id, EventRow.type == "RunEnd")
        if through_seq is not None:
            query = query.where(EventRow.seq <= through_seq)
        async with self._factory() as db:
            rows = (await db.execute(query.order_by(EventRow.seq.desc()).limit(MAX_CHAIN + 1))).scalars().all()
        if not rows or (through_seq is not None and int(rows[0].seq) != through_seq):
            return {}
        stored = dict(zip((int(r.seq) for r in rows), await self._payloads(rows), strict=True))
        done: dict[int, Snapshot] = {}

        async def snapshot(seq: int, hops: int) -> Snapshot:
            if seq in done:
                return done[seq]
            if hops > MAX_CHAIN:
                raise LookupError(f"session {session_id}: RunEnd {seq} is more than {MAX_CHAIN} deltas from a full one")
            if seq not in stored:
                stored.update(await self._run_end_payloads(session_id, seq))
            payload = stored.get(seq)
            if payload is None:
                raise LookupError(f"session {session_id}: no RunEnd at {seq} for a delta to stand on")
            depth = snapshots.depth(payload)
            if snapshots.is_delta(payload):
                base = await snapshot(int(payload["_base_seq"]), hops + 1)
                payload = snapshots.resolve(payload, base.messages)
            done[seq] = Snapshot(seq, depth, payload["messages"])
            return done[seq]

        await snapshot(int(rows[0].seq), 0)
        return done

    async def _run_end_payloads(self, session_id: str, seq: int) -> dict[int, dict]:
        async with self._factory() as db:
            rows = (
                await db.execute(
                    select(EventRow).where(
                        EventRow.session_id == session_id, EventRow.type == "RunEnd", EventRow.seq == seq
                    )
                )
            ).scalars().all()
        return dict(zip((int(r.seq) for r in rows), await self._payloads(rows), strict=True))

    async def unfinished_jobs(self, session_id: str) -> set[str]:
        """The session's background subagent jobs that started and have not ended."""
        events = await self.read_types([session_id], [JobStart.__name__, JobEnd.__name__])
        started = {e.payload.job_id for e in events if isinstance(e.payload, JobStart) and e.payload.kind == "subagent"}
        return started - {e.payload.job_id for e in events if isinstance(e.payload, JobEnd)}

    @property
    def blobs(self) -> BlobStore | None:
        """Where large payloads and lost sandboxes' logs are kept (``supervisor.journal.blobs``)."""
        return self._blobs

    async def read_blob(self, ref: str) -> bytes:
        """A blob stored with ``JournalWriter.put_blob``; ``FileNotFoundError`` if there is none."""
        if self._blobs is None:
            raise FileNotFoundError(ref)
        return await self._blobs.get(ref)

    async def _blob(self, ref: str) -> dict:
        if self._blobs is None:
            raise FileNotFoundError(f"blob ref {ref!r} but no blob store configured")
        return json.loads((await self._blobs.get(ref)).decode())
