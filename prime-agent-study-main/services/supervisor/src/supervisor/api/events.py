"""SSE event stream with Last-Event-ID resume.

Subscribe to live *before* journal replay; filter seq <= cursor so the
handover race yields neither gaps nor duplicates.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

from supervisor.api import deps
from supervisor.api.wire import envelope_to_wire
from supervisor.journal.reader import JournalReader
from supervisor.session import LiveSubscription, SessionManager, is_dropped_sentinel

_log = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/sessions", tags=["events"])

_HEARTBEAT_SECS = 20.0
_JOURNAL_POLL_SECS = 0.25


def _sse_data(wire_json: str, *, event_id: int) -> str:
    return f"id: {event_id}\ndata: {wire_json}\n\n"


def _sse_comment(text: str) -> str:
    return f": {text}\n\n"


async def iter_sse(
    *,
    session_id: str,
    cursor: int,
    manager: SessionManager,
    reader: JournalReader,
    is_disconnected: Callable[[], bool] | None = None,
    heartbeat_secs: float | None = None,
    max_events: int | None = None,
) -> AsyncIterator[str]:
    """Resumable SSE body. Subscribe before replay; skip seq <= cursor on live.

    ``max_events`` is a test hook to end the stream after N data events.
    """
    hb = _HEARTBEAT_SECS if heartbeat_secs is None else heartbeat_secs

    def _gone() -> bool:
        if is_disconnected is None:
            return False
        return is_disconnected()

    sub: LiveSubscription = manager.subscribe_live(session_id)
    live_cursor = cursor
    emitted = 0
    try:
        for env in await reader.read(session_id, since=cursor):
            if _gone():
                return
            wire = envelope_to_wire(env)
            yield _sse_data(wire.model_dump_json(), event_id=env.seq)
            live_cursor = env.seq
            emitted += 1
            if max_events is not None and emitted >= max_events:
                return

        while True:
            if sub.dropped or _gone():
                if sub.dropped:
                    _log.info("dropping slow SSE client session=%s", session_id)
                return
            try:
                item = await asyncio.wait_for(sub.queue.get(), timeout=hb)
            except asyncio.TimeoutError:
                yield _sse_comment("ping")
                continue
            if is_dropped_sentinel(item):
                return
            env = item  # type: ignore[assignment]
            if env.seq <= live_cursor:
                continue
            wire = envelope_to_wire(env)
            yield _sse_data(wire.model_dump_json(), event_id=env.seq)
            live_cursor = env.seq
            emitted += 1
            if max_events is not None and emitted >= max_events:
                return
    finally:
        manager.unsubscribe_live(session_id, sub)


async def iter_journal_sse(
    *,
    session_id: str,
    cursor: int,
    reader: JournalReader,
    is_disconnected: Callable[[], bool] | None = None,
    heartbeat_secs: float | None = None,
    poll_secs: float = _JOURNAL_POLL_SECS,
    max_events: int | None = None,
) -> AsyncIterator[str]:
    """Resumable SSE body that follows the journal, so it works for sessions running
    in any worker and from any API replica.

    shortcut: polls the journal; switch to Postgres LISTEN/NOTIFY if poll load shows up.
    """
    hb = _HEARTBEAT_SECS if heartbeat_secs is None else heartbeat_secs
    emitted, idle = 0, 0.0
    while is_disconnected is None or not is_disconnected():
        batch = await reader.read(session_id, since=cursor)
        for env in batch:
            yield _sse_data(envelope_to_wire(env).model_dump_json(), event_id=env.seq)
            cursor, emitted = env.seq, emitted + 1
            if max_events is not None and emitted >= max_events:
                return
        if batch:
            idle = 0.0
            continue
        if idle >= hb:
            yield _sse_comment("ping")
            idle = 0.0
        await asyncio.sleep(poll_secs)
        idle += poll_secs


@router.get("/{session_id}/events")
async def stream_events(
    session_id: str,
    request: Request,
    tenant_id: str = Depends(deps.current_tenant),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    await deps.require_session_access(tenant_id, session_id, request)
    manager = deps.get_manager(request)
    reader = deps.get_reader(request)

    cursor = -1
    if last_event_id is not None and last_event_id != "":
        try:
            cursor = int(last_event_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail="invalid Last-Event-ID"
            ) from exc

    async def gen() -> AsyncIterator[str]:
        body = (
            iter_journal_sse(session_id=session_id, cursor=cursor, reader=reader)
            if deps.get_workflows(request) is not None
            else iter_sse(session_id=session_id, cursor=cursor, manager=manager, reader=reader)
        )
        async for chunk in body:
            if await request.is_disconnected():
                return
            yield chunk

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
