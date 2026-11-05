"""The session's browser for a person: its live view, and their hand on it (``supervisor
.hands.browser.live``).

``GET /v1/sessions/{id}/browser/screen`` is the browser's current tab, whenever one is
open. ``POST /v1/sessions/{id}/browser/input`` is a person's click, typing, key or scroll
on it, then the tab after it: only while the agent is not working, so a person and the
agent never drive the browser at once. A person takes over when the agent asks them to
(a CAPTCHA, a code, a payment), and hands back with a message saying what they did.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import TypeAdapter, ValidationError

from supervisor.api import deps
from supervisor.hands.browser.live import Input, LiveRefused, Screen, act, screen
from supervisor.session.manager_tools import LiveBrowser
from supervisor.tools import WorkspaceReset, WorkspaceUnavailable

__all__ = ["router"]

router = APIRouter(prefix="/v1/sessions", tags=["browser"])

_INPUT: TypeAdapter[Input] = TypeAdapter(Input)
NO_SANDBOX = "the session's sandbox is not running: the browser is back when the agent next uses it"
AGENT_WORKING = "the agent is working in the browser: wait for its turn to end, or stop it, to take control"


@router.get("/{session_id}/browser/screen", response_model=Screen)
async def browser_screen(session_id: str, request: Request, tenant_id: str = Depends(deps.current_tenant)) -> Screen:
    """The browser's current tab, as a JPEG with its URL and title."""
    browser = await _browser(request, tenant_id, session_id)
    return await _live(lambda: screen(browser.workspace, browser.env))


@router.post("/{session_id}/browser/input", response_model=Screen)
async def browser_input(
    session_id: str,
    request: Request,
    body: Any = Body(...),
    tenant_id: str = Depends(deps.current_tenant),
) -> Screen:
    """A person's input on the browser's current tab, while the agent is not working; the
    tab after it."""
    try:
        action = _INPUT.validate_python(body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors(include_url=False, include_context=False, include_input=False)) from exc
    browser = await _browser(request, tenant_id, session_id)
    if browser.agent_working:
        raise HTTPException(status_code=409, detail=AGENT_WORKING)
    return await _live(lambda: act(browser.workspace, browser.env, action))


async def _browser(request: Request, tenant_id: str, session_id: str) -> LiveBrowser:
    await deps.require_session_access(tenant_id, session_id, request)
    browser = await deps.get_manager(request).live_browser(session_id)
    if browser is None:
        raise HTTPException(status_code=409, detail=NO_SANDBOX)
    return browser


async def _live(look: Callable[[], Awaitable[Screen]]) -> Screen:
    """``look``'s screen; a browser that cannot be shown, as why (409)."""
    try:
        shown = await look()
    except LiveRefused as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (WorkspaceUnavailable, WorkspaceReset) as exc:
        raise HTTPException(status_code=409, detail=NO_SANDBOX) from exc
    return shown
