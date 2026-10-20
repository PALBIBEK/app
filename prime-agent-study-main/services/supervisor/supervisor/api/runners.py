"""Customer runners: register them, and the endpoints a runner dials out to.

Registering takes the tenant's service identity and returns the runner's token
once. The runner's own endpoints (``next``, ``commands/{id}``) take that token
and the tenant id instead: a runner holds no service identity, and its token
opens nothing but its own queue.
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel

from supervisor.api import deps
from supervisor.hands.runners import RunnerHub

router = APIRouter(prefix="/v1/runners", tags=["runners"])

_NAME = re.compile(r"[a-z][a-z0-9_-]{0,63}")


class RunnerToken(BaseModel):
    name: str
    token: str


class RunnerOut(BaseModel):
    name: str
    online: bool


class CommandOut(BaseModel):
    id: str
    sandbox_id: str | None
    op: str
    args: dict[str, Any]


class CommandResult(BaseModel):
    ok: bool
    result: dict[str, Any] = {}


def _hub(request: Request) -> RunnerHub:
    hub = getattr(deps._state(request), "runners", None)
    if hub is None:
        raise HTTPException(status_code=503, detail="runners not configured")
    return hub


def _name(name: str) -> str:
    if not _NAME.fullmatch(name):
        raise HTTPException(status_code=422, detail="runner names: lowercase letters, digits, '-', '_'")
    return name


async def _runner(
    request: Request,
    name: str,
    authorization: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
) -> tuple[str, str]:
    """(tenant, runner) for a runner's own call, authenticated by its token."""
    token = deps._extract_bearer(authorization)
    if token is None or not x_tenant_id or not await _hub(request).verify(x_tenant_id, name, token):
        raise HTTPException(status_code=401, detail="unknown runner or wrong token")
    return x_tenant_id, name


@router.post("/{name}", response_model=RunnerToken)
async def register_runner(request: Request, name: str, tenant_id: str = Depends(deps.current_tenant)) -> RunnerToken:
    """Register a runner (or re-key one: its old token stops working). The token is shown once."""
    token = await _hub(request).register(tenant_id, _name(name))
    return RunnerToken(name=name, token=token)


@router.get("", response_model=list[RunnerOut])
async def list_runners(request: Request, tenant_id: str = Depends(deps.current_tenant)) -> list[RunnerOut]:
    return [RunnerOut(name=n, online=on) for n, on in await _hub(request).runners(tenant_id)]


@router.delete("/{name}", status_code=204)
async def remove_runner(request: Request, name: str, tenant_id: str = Depends(deps.current_tenant)) -> Response:
    if not await _hub(request).remove(tenant_id, _name(name)):
        raise HTTPException(status_code=404, detail="no such runner")
    return Response(status_code=204)


@router.get("/{name}/next", response_model=CommandOut | None)
async def next_command(
    request: Request,
    wait: float = Query(default=20.0, ge=0.0, le=55.0),
    runner: tuple[str, str] = Depends(_runner),
) -> Any:
    """The runner's next command (claimed for it), waiting up to ``wait`` seconds; 204 if none."""
    command = await _hub(request).next_command(*runner, wait_s=wait)
    if command is None:
        return Response(status_code=204)
    return CommandOut(id=command.id, sandbox_id=command.sandbox_id, op=command.op, args=command.args)


@router.post("/{name}/commands/{command_id}", status_code=204)
async def complete_command(
    request: Request, command_id: str, body: CommandResult, runner: tuple[str, str] = Depends(_runner)
) -> Response:
    if not await _hub(request).complete(*runner, command_id, ok=body.ok, result=body.result):
        raise HTTPException(status_code=404, detail="no such claimed command")
    return Response(status_code=204)
