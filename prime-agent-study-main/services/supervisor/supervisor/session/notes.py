"""The lessons an agent noted in its session (``note_for_session``), as its next runs read them.

A note is the result of a successful ``note_for_session`` call, so the session's journal is
where notes live: they outlast compaction (which shortens what the model reads, not the
journal) and go when the session is erased. ``session_notes`` reads them back: the session's
own, oldest first, the same note once, the latest ``MAX_NOTES``.
"""

from __future__ import annotations

from typing import Final

from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.agent.events import ToolEnd
from supervisor.db.models import RunTraceRow, SessionEvaluationRow, SessionRow
from supervisor.journal.reader import JournalReader
from supervisor.session.access import session_visibility
from supervisor.tools.builtin.note_for_session import NAME

__all__ = ["MAX_NOTES", "SHARED_NOTES", "composition_notes", "note_of", "session_notes"]

MAX_NOTES: Final = 20
"""Notes a session's prompt carries (the latest)."""
SHARED_NOTES: Final = 10
"""Notes from many sessions a reflection reads (``composition_notes``)."""
SCAN_SESSIONS: Final = 200
"""Recent sessions of an agent whose notes are looked at."""


def note_of(event: object) -> str | None:
    """The note a journal event holds: a ``note_for_session`` call that succeeded."""
    if not isinstance(event, ToolEnd) or event.tool_name != NAME or event.is_error:
        return None
    payload = event.result.get("payload") if isinstance(event.result, dict) else None
    note = payload.get("note") if isinstance(payload, dict) else None
    return note.strip() or None if isinstance(note, str) else None


async def session_notes(reader: JournalReader, session_id: str) -> list[str]:
    notes = [n for env in await reader.of_types(session_id, ["ToolEnd"]) if (n := note_of(env.payload))]
    return list(dict.fromkeys(notes))[-MAX_NOTES:]


async def composition_notes(
    factory: async_sessionmaker[AsyncSession],
    reader: JournalReader,
    *,
    tenant_id: str,
    principal_id: str | None,
    composition_id: str,
    exclude_sessions: list[str],
) -> list[dict[str, object]]:
    """What the agent noted in its recent sessions, the notes made in most sessions first,
    each with how many: the improvement loop reads them as unverified evidence. Sessions the
    optimizer must not read are left out: every experiment's replay (marked in
    ``session_evaluations``) and ``exclude_sessions`` (the held-out saved runs')."""
    query = (
        select(RunTraceRow.session_id)
        .join(SessionRow, SessionRow.id == RunTraceRow.session_id)
        .where(SessionRow.tenant_id == tenant_id, session_visibility(tenant_id, principal_id),
               RunTraceRow.composition_id == composition_id)
        .group_by(RunTraceRow.session_id)
        .order_by(func.max(RunTraceRow.started_ms).desc())
        .limit(SCAN_SESSIONS)
    )
    if exclude_sessions:
        query = query.where(RunTraceRow.session_id.not_in(exclude_sessions))
    query = query.where(~exists().where(SessionEvaluationRow.session_id == RunTraceRow.session_id))
    async with factory() as db:
        sessions = [str(s) for s in (await db.execute(query)).scalars()]
    seen: dict[str, set[str]] = {}
    for env in await reader.read_types(sessions, ["ToolEnd"]):
        if note := note_of(env.payload):
            seen.setdefault(note, set()).add(env.session_id)
    ranked = sorted(seen.items(), key=lambda kv: -len(kv[1]))[:SHARED_NOTES]
    return [{"note": note, "sessions": len(where)} for note, where in ranked]
