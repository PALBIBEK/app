"""A session as an AG-UI agent (``supervisor.agui``).

``POST /v1/sessions/{id}/agui`` is an AG-UI run: the body is the protocol's ``RunAgentInput``
(its thread is the session, its last user message is what the session is told), and the
response streams that run's events until it ends. A run whose ``forwardedProps`` carry a
person's action on a surface the agent drew (``a2uiAction.userAction``, AG-UI's A2UI
convention) tells the session that action instead (``supervisor.genui.actions``). ``GET /v1/sessions/{id}/agui`` follows the
session as our ``/events`` does, resumable by ``Last-Event-ID``: a client that drew the
conversation once keeps following it. Both are SSE in the protocol's own encoding
(``ag_ui.encoder.EventEncoder``); the last AG-UI event of each journalled one carries its seq as
the SSE id, so a reconnect never replays nor skips one.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator, Iterable
from typing import Any, Final

from ag_ui.core import BaseEvent, RunAgentInput, UserMessage
from ag_ui.encoder import EventEncoder
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import ValidationError

from supervisor.agent.events import Envelope, RunEnd
from supervisor.agui import AgUiTranslator
from supervisor.api import deps
from supervisor.api.events import SSE_PING, session_items, sse_response, stream_cursor
from supervisor.api.schemas import COMMAND_ID_MAX, PostMessageRequest
from supervisor.api.sessions import submit_message
from supervisor.api.stream import Ended, Item, Ping
from supervisor.genui.actions import JOURNAL_TYPES, Drawing, action_problem, action_text, drawings
from supervisor.journal.partials import Partial

__all__ = ["router"]

A2UI_ACTION: Final = ("a2uiAction", "userAction")
"""Where a run's ``forwardedProps`` carry a person's A2UI action."""

router = APIRouter(prefix="/v1/sessions", tags=["ag-ui"])


@router.get("/{session_id}/agui")
async def follow_session(
    session_id: str,
    request: Request,
    tenant_id: str = Depends(deps.current_tenant),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    since: int | None = Query(default=None, description="Send events after this seq"),
) -> StreamingResponse:
    """The session's AG-UI events after ``since`` (or ``Last-Event-ID``), then as they happen."""
    await deps.require_session_access(tenant_id, session_id, request)
    cursor = stream_cursor(last_event_id, since)
    items = session_items(request, session_id, cursor)
    return sse_response(request, _frames(items, AgUiTranslator(session_id)), EventEncoder().get_content_type())


@router.post("/{session_id}/agui")
async def run_session(session_id: str, request: Request, tenant_id: str = Depends(deps.current_tenant)) -> StreamingResponse:
    """An AG-UI run: the input's last user message (or the person's action it forwards), told
    to the session; then that run's events, until it ends. A message's command id is the
    run's (an action's, the drawing's it answers), so sending the same run again is the same
    message, not a second one, and streams that run again from its start."""
    run = _run_input(await request.json())
    if run.thread_id != session_id:
        raise HTTPException(status_code=400, detail=f"threadId is {run.thread_id!r}: this session is {session_id!r}")
    await deps.require_session_access(tenant_id, session_id, request)
    reader = deps.get_reader(request)
    cursor = await reader.last_seq(session_id)  # before it is told: nothing of the run is missed
    if (action := _forwarded_action(run.forwarded_props)) is not None:
        told = await _answer(request, session_id, action)
    else:
        told = PostMessageRequest(text=_last_user_text(run.messages), command_id=run.run_id)
    accepted = await submit_message(request, tenant_id, session_id, told)
    if (first := await reader.first_seq(session_id, accepted.turn_id)) is not None:
        cursor = min(cursor, first - 1)  # the run was sent before, and has begun
    translator = AgUiTranslator(session_id, run_ids={accepted.turn_id: run.run_id})
    items = _until_run_ends(session_items(request, session_id, cursor), accepted.turn_id)
    return sse_response(request, _frames(items, translator), EventEncoder().get_content_type())


def _run_input(body: Any) -> RunAgentInput:
    try:
        return RunAgentInput.model_validate(body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors(include_url=False, include_context=False)) from exc


def _forwarded_action(props: Any) -> Any | None:
    """The person's action a run's ``forwardedProps`` carry, if they carry one."""
    outer = props.get(A2UI_ACTION[0]) if isinstance(props, dict) else None
    return outer.get(A2UI_ACTION[1]) if isinstance(outer, dict) else None


async def _answer(request: Request, session_id: str, action: Any) -> PostMessageRequest:
    """``action`` as the message the session is told: 422 unless it is from a button on a
    surface the session shows, 409 if that drawing was answered otherwise."""
    surfaces = drawings(await deps.get_reader(request).of_types(session_id, JOURNAL_TYPES))
    if (problem := action_problem(action, surfaces)) is not None:
        raise HTTPException(status_code=422, detail=f"the action cannot be taken: {problem}")
    drawing = surfaces[action["surfaceId"]]
    told = PostMessageRequest(text=action_text(action), command_id=_answer_id(drawing))
    try:
        await deps.recorded_command(request, session_id, told.command_id, told.text)
    except HTTPException as exc:
        if exc.status_code == 409:
            detail = f"surface {drawing.surface_id!r} was answered already: the agent has to draw it again for another answer"
            raise HTTPException(status_code=409, detail=detail) from exc
        raise
    return told


def _answer_id(drawing: Drawing) -> str:
    """One command id per surface and drawing: the same click sent again is the same message,
    and another answer to the same drawing a conflict (a surface id too long for a command id
    is named by its hash)."""
    named = f"a2ui:{drawing.seq}:{drawing.surface_id}"
    return named if len(named) <= COMMAND_ID_MAX else f"a2ui:{drawing.seq}:{hashlib.sha256(drawing.surface_id.encode()).hexdigest()}"


def _last_user_text(messages: Iterable[Any]) -> str:
    """What the run asks: the text of its last user message (its text parts, joined)."""
    for message in reversed(list(messages)):
        if isinstance(message, UserMessage):
            content = message.content
            text = content if isinstance(content, str) else "".join(getattr(p, "text", "") or "" for p in content)
            if text.strip():
                return text
    raise HTTPException(status_code=422, detail="the run has no user message with text: there is nothing to tell the session")


async def _until_run_ends(items: AsyncIterator[Item], turn_id: str) -> AsyncIterator[Item]:
    """``items`` up to and including the end of the run ``turn_id``."""
    async for item in items:
        yield item
        if isinstance(item, Envelope) and isinstance(item.payload, RunEnd) and item.turn_id == turn_id:
            return


async def _frames(items: AsyncIterator[Item], translator: AgUiTranslator) -> AsyncIterator[str]:
    encoder = EventEncoder()
    async for item in items:
        if isinstance(item, Envelope):
            yield _encoded(encoder, translator.envelope(item), seq=item.seq)
        elif isinstance(item, Partial):
            yield _encoded(encoder, translator.partial(item), seq=None)
        elif isinstance(item, Ping):
            yield SSE_PING
        elif isinstance(item, Ended):
            return


def _encoded(encoder: EventEncoder, events: list[BaseEvent], *, seq: int | None) -> str:
    """``events`` as SSE frames; the last one carries ``seq``, a journalled event's, so a
    reconnect resumes after it (a partial reply's carry none: it is not in the journal)."""
    frames = [encoder.encode(event) for event in events]
    if seq is not None and frames:
        frames[-1] = f"id: {seq}\n{frames[-1]}"
    return "".join(frames)
