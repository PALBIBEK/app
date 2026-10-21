"""Authenticated agent-workspace BFF routes (D9) — durable SoT via supervisor.

Not tenant Settings / org chrome. Browser never holds AI service creds.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import Response as RawResponse
from sqlalchemy import func, select

from prime_agent.config import get_settings
from prime_agent.db.models.user import User
from prime_agent.schemas.ai_identity import TenantPrincipal
from prime_agent.schemas.workspace import (
    EnsureScopeResponse,
    InviteScopeMemberIn,
    ScopeMemberOut,
    WorkspaceFileOut,
    WorkspaceRehydrateResponse,
    WorkspaceUploadCompleteIn,
    WorkspaceUploadStartIn,
    WorkspaceUploadStartOut,
)
from prime_agent.services.session_service import (
    SessionNotFoundError,
    raise_session_not_found_http,
)
from prime_agent.services.workspace_service import WorkspaceService
from prime_agent.web.dependencies import CurrentUser, DbSession

router = APIRouter()


def _service() -> WorkspaceService:
    return WorkspaceService.from_settings(get_settings())


@router.post(
    "/scopes/{scope_id}/ensure",
    response_model=EnsureScopeResponse,
)
async def ensure_agent_scope(
    scope_id: str,
    current_user: CurrentUser,
) -> EnsureScopeResponse:
    """Bootstrap durable scope + owner grants (upload-before-session path)."""
    identity = TenantPrincipal.from_user(current_user)
    return await _service().ensure_scope(scope_id=scope_id, identity=identity)


@router.get(
    "/scopes/{scope_id}/files",
    response_model=list[WorkspaceFileOut],
)
async def list_agent_workspace_files(
    scope_id: str,
    current_user: CurrentUser,
) -> list[WorkspaceFileOut]:
    identity = TenantPrincipal.from_user(current_user)
    return await _service().list_files(scope_id=scope_id, identity=identity)


@router.get("/scopes/{scope_id}/files/{path:path}")
async def get_agent_workspace_file(
    scope_id: str,
    path: str,
    current_user: CurrentUser,
) -> RawResponse:
    identity = TenantPrincipal.from_user(current_user)
    data = await _service().get_file_bytes(
        scope_id=scope_id, path=path, identity=identity
    )
    return RawResponse(content=data, media_type="application/octet-stream")


@router.put(
    "/scopes/{scope_id}/files/{path:path}",
    response_model=WorkspaceFileOut,
)
async def put_agent_workspace_file(
    scope_id: str,
    path: str,
    current_user: CurrentUser,
    upload: Annotated[UploadFile, File(...)],
    mime_type: str | None = Query(default=None),
    expected_version: int | None = Query(default=None),
) -> WorkspaceFileOut:
    """Upload through the API (multipart). The browser uses it only when storage cannot take a
    direct upload (``POST .../uploads`` answered ``proxy``); read in chunks, up to a limit."""
    identity = TenantPrincipal.from_user(current_user)
    limit = get_settings().workspace_max_upload_bytes
    chunks: list[bytes] = []
    total = 0
    while chunk := await upload.read(1024 * 1024):
        total += len(chunk)
        if total > limit:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail={"error_code": "FILE_TOO_LARGE",
                        "message": f"this workspace takes files up to {limit} bytes"},
            )
        chunks.append(chunk)
    body = b"".join(chunks)
    resolved_mime = mime_type or upload.content_type
    return await _service().put_file(
        scope_id=scope_id,
        path=path,
        data=body,
        identity=identity,
        mime_type=resolved_mime,
        expected_version=expected_version,
    )


@router.post("/scopes/{scope_id}/uploads", response_model=WorkspaceUploadStartOut)
async def start_agent_workspace_upload(
    scope_id: str,
    body: WorkspaceUploadStartIn,
    current_user: CurrentUser,
) -> WorkspaceUploadStartOut:
    """Step 1 of a direct upload: a short-lived URL the browser PUTs the file to itself."""
    return await _service().start_upload(
        scope_id=scope_id, body=body, identity=TenantPrincipal.from_user(current_user)
    )


@router.post("/scopes/{scope_id}/uploads/complete", response_model=WorkspaceFileOut)
async def complete_agent_workspace_upload(
    scope_id: str,
    body: WorkspaceUploadCompleteIn,
    current_user: CurrentUser,
) -> WorkspaceFileOut:
    """Step 3: the supervisor checks what arrived and records the file."""
    return await _service().complete_upload(
        scope_id=scope_id, upload_token=body.upload_token,
        identity=TenantPrincipal.from_user(current_user),
    )


@router.delete(
    "/scopes/{scope_id}/files/{path:path}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_agent_workspace_file(
    scope_id: str,
    path: str,
    current_user: CurrentUser,
) -> Response:
    identity = TenantPrincipal.from_user(current_user)
    await _service().delete_file(scope_id=scope_id, path=path, identity=identity)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/scopes/{scope_id}/members",
    response_model=list[ScopeMemberOut],
)
async def list_agent_scope_members(
    scope_id: str,
    current_user: CurrentUser,
    db: DbSession,
) -> list[ScopeMemberOut]:
    identity = TenantPrincipal.from_user(current_user)
    members = await _service().list_scope_members(scope_id=scope_id, identity=identity)
    users = {
        u.id: u
        for u in (
            await db.execute(select(User).where(User.id.in_([m.principal_id for m in members])))
        ).scalars()
    }
    return [
        m.model_copy(update={"email": users[m.principal_id].email, "name": users[m.principal_id].name})
        if m.principal_id in users
        else m
        for m in members
    ]


@router.post(
    "/scopes/{scope_id}/members",
    response_model=ScopeMemberOut,
    status_code=status.HTTP_201_CREATED,
)
async def invite_agent_scope_member(
    scope_id: str,
    body: InviteScopeMemberIn,
    current_user: CurrentUser,
    db: DbSession,
) -> ScopeMemberOut:
    identity = TenantPrincipal.from_user(current_user)
    invitee: User | None = None
    if body.email:
        invitee = (
            await db.execute(select(User).where(func.lower(User.email) == body.email.strip().lower()))
        ).scalar_one_or_none()
        if invitee is None:
            raise HTTPException(status_code=404, detail="no user with that email")
        body = body.model_copy(update={"principal_id": invitee.id, "email": None})
    member = await _service().invite_scope_member(scope_id=scope_id, identity=identity, body=body)
    if invitee is not None:
        member = member.model_copy(update={"email": invitee.email, "name": invitee.name})
    return member


@router.delete(
    "/scopes/{scope_id}/members/{principal_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_agent_scope_member(
    scope_id: str,
    principal_id: str,
    current_user: CurrentUser,
) -> Response:
    identity = TenantPrincipal.from_user(current_user)
    await _service().revoke_scope_member(
        scope_id=scope_id,
        principal_id=principal_id,
        identity=identity,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/sessions/{session_id}/workspace/rehydrate",
    response_model=WorkspaceRehydrateResponse,
)
async def rehydrate_session_agent_workspace(
    session_id: str,
    db: DbSession,
    current_user: CurrentUser,
) -> WorkspaceRehydrateResponse:
    identity = TenantPrincipal.from_user(current_user)
    try:
        return await _service().rehydrate_session(
            session_id=session_id,
            user_id=current_user.id,
            identity=identity,
            db=db,
        )
    except SessionNotFoundError as exc:
        raise_session_not_found_http(exc)
