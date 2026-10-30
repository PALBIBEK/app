"""Erasing the calling principal: what the supervisor and the memory service keep of them.

The product calls this when a person deletes their account, after erasing their sessions
one by one (``DELETE /v1/sessions/{id}/data``); anything left is erased here too. It acts
as the verified caller only, and is safe to repeat: a second call finds nothing.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from supervisor.api import deps
from supervisor.api.sessions import stop_for_erasure
from supervisor.memory.client import MemoryRefused, MemoryServiceNotConfigured, MemoryUnavailable
from supervisor.seams.services import Principal

router = APIRouter(prefix="/v1/principals", tags=["principals"])

STOPPING_AT_ONCE = 8
"""Sessions ended at the same time while erasing a principal."""


class PrincipalErasedOut(BaseModel):
    sessions: int
    scopes: int
    scopes_handed_on: int
    workspace_files: int
    secrets: int
    notebooks: int


@router.delete("/me/data", response_model=PrincipalErasedOut)
async def erase_me(
    request: Request,
    principal: Principal = Depends(deps.current_principal),
) -> PrincipalErasedOut:
    """End every session the caller owns, then erase them, the caller's long-term memory,
    the workspaces only they belong to, and their place in everyone else's
    (``supervisor.session.erase_principal``). When the caller is their own tenant, the
    tenant's secrets go too."""
    tenant_id = principal.tenant_id or principal.id
    manager = deps.get_manager(request)
    sessions = await manager.sessions_owned_by(tenant_id, principal.id)
    limit = asyncio.Semaphore(STOPPING_AT_ONCE)

    async def stop(session_id: str) -> None:
        async with limit:
            await stop_for_erasure(request, session_id)

    await asyncio.gather(*(stop(session_id) for session_id in sessions))
    notebooks = await _erase_memory(request, tenant_id, principal.id)
    erased = await manager.erase_principal(tenant_id, principal.id)
    return PrincipalErasedOut(
        sessions=erased.sessions,
        scopes=erased.scopes,
        scopes_handed_on=erased.scopes_handed_on,
        workspace_files=erased.workspace_files,
        secrets=erased.secrets,
        notebooks=notebooks,
    )


async def _erase_memory(request: Request, tenant_id: str, principal_id: str) -> int:
    """The principal's notebooks erased by the memory service (none kept when there is none)."""
    platform = getattr(deps._state(request), "memory", None)
    if platform is None:
        return 0
    try:
        return await platform.store.erase_owner(
            tenant_id=tenant_id, owner=principal_id, actor=principal_id, reason="the principal erased their data"
        )
    except MemoryServiceNotConfigured:
        return 0
    except (MemoryUnavailable, MemoryRefused) as exc:
        raise HTTPException(
            status_code=503, detail=f"long-term memory could not be erased; erase again shortly ({exc})"
        ) from exc
