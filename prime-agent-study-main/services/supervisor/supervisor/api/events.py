"""Our wire's SSE event stream, with Last-Event-ID resume.

What a session says comes from ``supervisor.api.stream``'s followers; this writes it as our
wire's frames: each journalled event with its seq as the SSE id, the reply being written as
a ``partial_output`` event without one (it is not in the journal, so a reconnect resumes after
the last journalled event and never replays it), a comment to keep the connection open, and
``event: end`` once a session that has ended has said everything, so a client stops
reconnecting on it.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse

from supervisor.agent.events import Envelope
from supervisor.api import deps
from supervisor.api.stream import (
    ENDED_CHECK_SECS,
    JOURNAL_POLL_SECS,
    PAGE,
    Ended,
    Item,
    Ping,
    follow_journal,
    follow_live,
)
from supervisor.api.wire import WIRE_VERSION, envelope_to_wire
from supervisor.journal.partials import Partial, PartialOutputs
from supervisor.journal.reader import JournalReader
from supervisor.journal.watch import JournalWatch
from supervisor.session import SessionManager
from supervisor.temporal.contracts import CLOSED_STATUSES

__all__ = ["PAGE", "SSE_END", "iter_journal_sse", "iter_sse", "sse_response", "stream_cursor"]

router = APIRouter(prefix="/v1/sessions", tags=["events"])

_HEARTBEAT_SECS = 20.0
"""A quiet stream's keep-alive: a comment after this long with nothing to send."""
SSE_END = "event: end\ndata: {}\n\n"
"""The last frame of the stream of a session that has ended: nothing will follow."""
SSE_PING = ": ping\n\n"


def _sse_data(wire_json: str, *, event_id: int) -> str:
    return f"id: {event_id}\ndata: {wire_json}\n\n"


def _partial_wire(partial: Partial, cursor: int) -> str:
    """A partial in the wire shape; ``seq`` is the last journalled one (not a position)."""
    return json.dumps(
        {
            "v": WIRE_VERSION,
            "seq": cursor,
            "event_id": f"partial:{partial.run_id}:{partial.step}",
            "turn_id": partial.run_id,
            "type": "partial_output",
            "ts_ms": partial.updated_ms,
            "data": {"step": partial.step, "text": partial.text, "thinking": partial.thinking},
        },
        ensure_ascii=False,
    )


async def _frames(items: AsyncIterator[Item], cursor: int, max_events: int | None) -> AsyncIterator[str]:
    """``items`` as our wire's SSE frames; ``max_events`` (a test hook) ends it after N events."""
    emitted = 0
    async for item in items:
        if isinstance(item, Envelope):
            yield _sse_data(envelope_to_wire(item).model_dump_json(), event_id=item.seq)
            cursor, emitted = item.seq, emitted + 1
            if max_events is not None and emitted >= max_events:
                return
        elif isinstance(item, Partial):
            yield f"data: {_partial_wire(item, cursor)}\n\n"
        elif isinstance(item, Ping):
            yield SSE_PING
        elif isinstance(item, Ended):
            yield SSE_END


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
    """An in-process session's SSE body (``follow_live``)."""
    live = follow_live(
        session_id=session_id,
        cursor=cursor,
        manager=manager,
        reader=reader,
        is_disconnected=is_disconnected,
        heartbeat_secs=_HEARTBEAT_SECS if heartbeat_secs is None else heartbeat_secs,
    )
    async for frame in _frames(live, cursor, max_events):
        yield frame


async def iter_journal_sse(
    *,
    session_id: str,
    cursor: int,
    reader: JournalReader,
    is_disconnected: Callable[[], Awaitable[bool]] | None = None,
    has_ended: Callable[[], Awaitable[bool]] | None = None,
    heartbeat_secs: float | None = None,
    poll_secs: float = JOURNAL_POLL_SECS,
    ended_check_secs: float = ENDED_CHECK_SECS,
    max_events: int | None = None,
    partials: PartialOutputs | None = None,
    watch: JournalWatch | None = None,
) -> AsyncIterator[str]:
    """A session's SSE body that follows its journal (``follow_journal``), so it works for
    sessions running in any worker and from any API replica."""
    items = follow_journal(
        session_id=session_id,
        cursor=cursor,
        reader=reader,
        is_disconnected=is_disconnected,
        has_ended=has_ended,
        heartbeat_secs=_HEARTBEAT_SECS if heartbeat_secs is None else heartbeat_secs,
        poll_secs=poll_secs,
        ended_check_secs=ended_check_secs,
        partials=partials,
        watch=watch,
    )
    async for frame in _frames(items, cursor, max_events):
        yield frame


def session_items(request: Request, session_id: str, cursor: int) -> AsyncIterator[Item]:
    """What the session says after ``cursor``, as the deployment serves it: its journal
    (sessions on Temporal, in any worker) or its manager's live queue (in-process)."""
    manager = deps.get_manager(request)
    reader = deps.get_reader(request)
    if deps.get_workflows(request) is None:
        return follow_live(session_id=session_id, cursor=cursor, manager=manager, reader=reader, heartbeat_secs=_HEARTBEAT_SECS)

    async def has_ended() -> bool:
        status = await manager.session_status(session_id)
        return status is None or status in CLOSED_STATUSES  # None: erased

    return follow_journal(
        session_id=session_id,
        cursor=cursor,
        reader=reader,
        is_disconnected=request.is_disconnected,
        has_ended=has_ended,
        heartbeat_secs=_HEARTBEAT_SECS,
        partials=PartialOutputs(reader.session_factory),
        watch=deps.get_watch(request),
    )


def sse_response(request: Request, frames: AsyncIterator[str], media_type: str = "text/event-stream") -> StreamingResponse:
    """``frames`` as a streaming response that stops when its client goes away."""

    async def body() -> AsyncIterator[str]:
        async for frame in frames:
            if await request.is_disconnected():
                return
            yield frame

    return StreamingResponse(
        body(),
        media_type=media_type,
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


def stream_cursor(last_event_id: str | None, since: int | None) -> int:
    """The last seq the client has: events after it are sent; -1 means from the start.

    ``Last-Event-ID`` is what a reconnecting EventSource sends, so it wins; the
    ``since`` query parameter is for the first connect, when a browser cannot
    set headers. (It used to be ignored without a word, F34.)
    """
    if last_event_id:
        try:
            return _valid(int(last_event_id), "Last-Event-ID")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid Last-Event-ID") from exc
    return -1 if since is None else _valid(since, "since")


def _valid(cursor: int, name: str) -> int:
    if cursor < -1:
        raise HTTPException(status_code=400, detail=f"{name} must be -1 or a seq")
    return cursor


@router.get("/{session_id}/events")
async def stream_events(
    session_id: str,
    request: Request,
    tenant_id: str = Depends(deps.current_tenant),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    since: int | None = Query(default=None, description="Send events after this seq"),
) -> StreamingResponse:
    await deps.require_session_access(tenant_id, session_id, request)
    cursor = stream_cursor(last_event_id, since)
    return sse_response(request, _frames(session_items(request, session_id, cursor), cursor, None))


@router.get("/{session_id}/hand-logs/{ref}")
async def hand_logs(
    session_id: str, ref: str, request: Request, tenant_id: str = Depends(deps.current_tenant)
) -> Response:
    """What a lost hand's provider recorded (``HandLost.logs_ref``), saved before it was destroyed."""
    await deps.require_session_access(tenant_id, session_id, request)
    data = await deps.get_reader(request).hand_logs(session_id, ref)
    if data is None:
        raise HTTPException(status_code=404, detail="no such hand logs in this session")
    return Response(content=data, media_type="text/plain; charset=utf-8")
