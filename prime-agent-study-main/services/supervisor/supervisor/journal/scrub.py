"""Mask the credentials stored before the journal masked what it stores.

Since #109 nothing is journalled with a credential in it, but journals written before
still hold them: in event payloads, in the large payloads and sandbox logs kept in the
blob store, in the tool outbox (a call's arguments and result) and in session titles.
This pass rewrites what masking changes and nothing else:

    python -m supervisor scrub            # dry run (supervisor.scrub_cli): counts what would change
    python -m supervisor scrub --apply    # rewrites it
    python -m supervisor scrub --apply --after <session_id>:<seq>   # carry on from there

It is safe to stop and run again (masking what is masked changes nothing), and it prints
where it is so a stopped run can carry on. It runs while the platform runs: a row it
rewrites is one no one writes any more (the journal is append-only).

A blob is named by its content's hash, so a masked one is stored under a new name, its
rows point there, and the old one is deleted once a whole pass left no row naming it.
RunEnd deltas stay consistent: a delta keeps a count of its base's messages and masking
is string by string, so the masked base and the masked delta resolve to the masked
conversation.

Afterwards, VACUUM ``events``, ``tool_outbox`` and ``session_index``: Postgres keeps a
rewritten row's old version in its files until then. It cannot reach backups or WAL
archives taken before: a credential that was journalled must be rotated.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.db.models import EventRow, SessionIndexRow, ToolOutboxRow
from supervisor.journal.blobs import BlobStore, blob_ref
from supervisor.journal.secrets import mask, mask_bytes, mask_text

__all__ = ["ScrubReport", "scrub"]

logger = logging.getLogger(__name__)

_BATCH = 500


@dataclass
class ScrubReport:
    events: int = 0  # rows read
    events_masked: int = 0
    blobs_masked: int = 0
    blobs_deleted: int = 0
    outbox_masked: int = 0
    titles_masked: int = 0
    position: tuple[str, int] | None = None  # the last event read
    replaced: dict[str, str] = field(default_factory=dict)  # old blob ref → masked one


async def scrub(
    factory: async_sessionmaker[AsyncSession],
    blobs: BlobStore | None,
    *,
    apply: bool = False,
    after: tuple[str, int] | None = None,
    batch: int = _BATCH,
    progress: Callable[[ScrubReport], None] | None = None,
) -> ScrubReport:
    """One pass over every journal (from ``after``); with ``apply`` False only counts."""
    report = ScrubReport(position=after)
    while True:
        async with factory() as db:
            query = select(EventRow).order_by(EventRow.session_id, EventRow.seq).limit(batch)
            if report.position is not None:
                query = query.where(tuple_(EventRow.session_id, EventRow.seq) > tuple_(*report.position))
            rows = (await db.execute(query)).scalars().all()
            if not rows:
                break
            for row in rows:
                report.events += 1
                payload = await _masked_payload(row.payload, blobs, report, apply=apply)
                if payload is not row.payload:
                    report.events_masked += 1
                    if apply:
                        row.payload = payload
            if apply:
                await db.commit()
            report.position = (rows[-1].session_id, int(rows[-1].seq))
        if progress is not None:
            progress(report)
    await _outbox_and_titles(factory, report, apply=apply)
    if apply and blobs is not None and report.position is not None and after is None:
        report.blobs_deleted = await _delete_replaced(factory, blobs, report.replaced)
    return report


async def _masked_payload(
    payload: dict[str, Any], blobs: BlobStore | None, report: ScrubReport, *, apply: bool
) -> dict[str, Any]:
    """``payload`` as it is stored once masked (itself when nothing changes)."""
    out = mask(payload)
    for key in ("_blob_ref", "logs_ref"):
        ref = payload.get(key)
        if not isinstance(ref, str) or blobs is None:
            continue
        new_ref = await _masked_blob(ref, blobs, report, apply=apply, logs=key == "logs_ref")
        if new_ref != ref:
            out = dict(out)
            out[key] = new_ref
            if key == "logs_ref" and apply:
                out["logs_bytes"] = len(await blobs.get(new_ref))
    return out


async def _masked_blob(ref: str, blobs: BlobStore, report: ScrubReport, *, apply: bool, logs: bool) -> str:
    """The ref the blob ``ref`` names is kept under once masked."""
    if ref in report.replaced:
        return report.replaced[ref]
    try:
        data = await blobs.get(ref)
    except FileNotFoundError:
        return ref
    if logs:
        masked = mask_bytes(data)
    else:
        decoded = json.loads(data.decode())
        new = mask(decoded)
        masked = data if new is decoded else json.dumps(new, separators=(",", ":"), allow_nan=False).encode()
    if masked == data:
        return ref
    new_ref = blob_ref(masked)
    report.blobs_masked += 1
    if apply:
        await blobs.put(new_ref, masked)
    report.replaced[ref] = new_ref
    return new_ref


async def _outbox_and_titles(factory: async_sessionmaker[AsyncSession], report: ScrubReport, *, apply: bool) -> None:
    last = ""
    while True:
        async with factory() as db:
            rows = (
                await db.execute(
                    select(ToolOutboxRow).where(ToolOutboxRow.id > last).order_by(ToolOutboxRow.id).limit(_BATCH)
                )
            ).scalars().all()
            if not rows:
                break
            for row in rows:
                arguments, result = mask(row.arguments), mask(row.result)
                if arguments is not row.arguments or result is not row.result:
                    report.outbox_masked += 1
                    if apply:
                        row.arguments, row.result = arguments, result
            last = rows[-1].id
            if apply:
                await db.commit()
    async with factory() as db:
        titled = (await db.execute(select(SessionIndexRow).where(SessionIndexRow.title.is_not(None)))).scalars().all()
        for idx in titled:
            masked = mask_text(idx.title or "")
            if masked != idx.title:
                report.titles_masked += 1
                if apply:
                    idx.title = masked
        if apply:
            await db.commit()


async def _delete_replaced(factory: async_sessionmaker[AsyncSession], blobs: BlobStore, replaced: dict[str, str]) -> int:
    """Delete each replaced blob no row names any more (a whole pass ran: none should)."""
    old = sorted(replaced)
    named: set[str] = set()
    async with factory() as db:
        for i in range(0, len(old), _BATCH):
            chunk = old[i : i + _BATCH]
            for key in ("_blob_ref", "logs_ref"):
                column = EventRow.payload[key].as_string()
                named.update(await db.scalars(select(column).where(column.in_(chunk))))
    deleted = 0
    for ref in old:
        if ref not in named:
            deleted += await blobs.delete(ref)
    return deleted
