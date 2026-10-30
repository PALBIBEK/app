"""A session's attachments, its runs' inputs and its artifacts (supervisor.session.files)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field

from supervisor.api import deps
from supervisor.seams.services import (
    CasConflict,
    ForbiddenError,
    Grant,
    GrantDenied,
    NotScopeMember,
    Principal,
    UnauthorizedError,
    WorkspacePathEscape,
    WorkspaceQuotaExceeded,
)
from supervisor.seams.services.session_workspace import is_reserved
from supervisor.session.files import (
    Artifact,
    ArtifactNotFound,
    Attachment,
    FileNotInWorkspace,
    SessionFiles,
)

router = APIRouter(prefix="/v1/sessions", tags=["session-files"])


class AttachIn(BaseModel):
    path: str = Field(min_length=1, max_length=1024)
    version: int | None = Field(default=None, ge=1)
    scope_id: str | None = Field(default=None, description="Defaults to the session's workspace")


class AttachmentOut(BaseModel):
    id: str
    scope_id: str
    path: str
    version: int
    content_sha256: str
    size_bytes: int
    mime_type: str | None
    name: str
    sandbox_path: str
    attached_by: str | None
    created_at: str


class ArtifactOut(BaseModel):
    id: str
    turn_id: str
    name: str
    content_sha256: str
    size_bytes: int
    mime_type: str | None
    created_at: str
    saved_scope_id: str | None
    saved_path: str | None
    saved_version: int | None


class SaveIn(BaseModel):
    path: str = Field(min_length=1, max_length=1024, description="Where in the workspace, e.g. reports/q4.pdf")
    scope_id: str | None = Field(default=None, description="Defaults to the session's workspace")
    expected_version: int | None = Field(default=None, ge=0)


class SavedOut(BaseModel):
    artifact: ArtifactOut
    path: str
    version: int
    scope_id: str


class RunInputOut(BaseModel):
    kind: str
    scope_id: str
    path: str
    version: int
    content_sha256: str


def _attachment_out(a: Attachment) -> AttachmentOut:
    return AttachmentOut(
        id=a.id, scope_id=a.scope_id, path=a.path, version=a.version, content_sha256=a.content_sha256,
        size_bytes=a.size_bytes, mime_type=a.mime_type, name=a.name, sandbox_path=a.sandbox_path,
        attached_by=a.attached_by, created_at=a.created_at.isoformat(),
    )


def _artifact_out(a: Artifact) -> ArtifactOut:
    return ArtifactOut(
        id=a.id, turn_id=a.turn_id, name=a.name, content_sha256=a.content_sha256, size_bytes=a.size_bytes,
        mime_type=a.mime_type, created_at=a.created_at.isoformat(), saved_scope_id=a.saved_scope_id,
        saved_path=a.saved_path, saved_version=a.saved_version,
    )


def _files(request: Request) -> SessionFiles:
    files = getattr(deps.get_manager(request), "files", None)
    if files is None:
        raise HTTPException(status_code=503, detail="session files need a workspace store")
    return files  # type: ignore[no-any-return]


async def _session(request: Request, tenant_id: str, session_id: str) -> Any:
    """The session row, once the caller may see the session (else 404)."""
    await deps.require_session_access(tenant_id, session_id, request)
    row = await deps.get_manager(request)._get_session_row(session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="session not found")
    return row


async def _grant(
    request: Request, scope_id: str, grant: Grant, authorization: str | None,
    x_tenant_id: str | None, x_principal_id: str | None,
) -> Principal:
    try:
        principal = await deps.current_principal(
            request, authorization=authorization, x_tenant_id=x_tenant_id, x_principal_id=x_principal_id
        )
        return await deps.get_authz(request).require(principal, scope_id, grant)
    except UnauthorizedError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except (GrantDenied, NotScopeMember, ForbiddenError) as exc:
        raise HTTPException(status_code=404, detail="not found") from exc


def _scope(row: Any, given: str | None) -> str:
    scope = given or getattr(row, "scope_id", None)
    if not scope:
        raise HTTPException(status_code=422, detail="this session has no workspace; name scope_id")
    return str(scope)


@router.get("/{session_id}/attachments", response_model=list[AttachmentOut])
async def list_attachments(
    session_id: str, request: Request, tenant_id: str = Depends(deps.current_tenant)
) -> list[AttachmentOut]:
    await _session(request, tenant_id, session_id)
    return [_attachment_out(a) for a in await _files(request).attachments(session_id)]


@router.post("/{session_id}/attachments", response_model=AttachmentOut, status_code=201)
async def attach_file(
    session_id: str,
    body: AttachIn,
    request: Request,
    tenant_id: str = Depends(deps.current_tenant),
    authorization: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
    x_principal_id: str | None = Header(default=None),
) -> AttachmentOut:
    """Attach a workspace file to the conversation, pinned to a version (default: current)."""
    row = await _session(request, tenant_id, session_id)
    scope = _scope(row, body.scope_id)
    principal = await _grant(request, scope, Grant.WORKSPACE_READ, authorization, x_tenant_id, x_principal_id)
    try:
        found = await _files(request).attach(
            session_id, scope, body.path, version=body.version, attached_by=principal.id
        )
    except FileNotInWorkspace as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except WorkspacePathEscape as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _attachment_out(found)


@router.delete("/{session_id}/attachments/{attachment_id}", status_code=204)
async def detach_file(
    session_id: str, attachment_id: str, request: Request, tenant_id: str = Depends(deps.current_tenant)
) -> Response:
    await _session(request, tenant_id, session_id)
    if not await _files(request).detach(session_id, attachment_id):
        raise HTTPException(status_code=404, detail="not attached")
    return Response(status_code=204)


@router.get("/{session_id}/artifacts", response_model=list[ArtifactOut])
async def list_artifacts(
    session_id: str, request: Request, tenant_id: str = Depends(deps.current_tenant)
) -> list[ArtifactOut]:
    """Files this session's runs made for the user, oldest first."""
    await _session(request, tenant_id, session_id)
    return [_artifact_out(a) for a in await _files(request).artifacts(session_id)]


@router.get("/{session_id}/artifacts/{artifact_id}/content")
async def artifact_content(
    session_id: str, artifact_id: str, request: Request, tenant_id: str = Depends(deps.current_tenant)
) -> Response:
    await _session(request, tenant_id, session_id)
    try:
        found, data = await _files(request).artifact_bytes(session_id, artifact_id)
    except ArtifactNotFound as exc:
        raise HTTPException(status_code=404, detail="not found") from exc
    return Response(content=data, media_type=found.mime_type or "application/octet-stream")


@router.post("/{session_id}/artifacts/{artifact_id}/save", response_model=SavedOut)
async def save_artifact(
    session_id: str,
    artifact_id: str,
    body: SaveIn,
    request: Request,
    tenant_id: str = Depends(deps.current_tenant),
    authorization: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
    x_principal_id: str | None = Header(default=None),
) -> SavedOut:
    """Save a run's output into a workspace folder: a new version whose source is the artifact."""
    row = await _session(request, tenant_id, session_id)
    scope = _scope(row, body.scope_id)
    if is_reserved(body.path):
        raise HTTPException(status_code=400, detail="save it to a workspace folder, not a session folder")
    principal = await _grant(request, scope, Grant.WORKSPACE_WRITE, authorization, x_tenant_id, x_principal_id)
    try:
        artifact, record = await _files(request).save(
            session_id, artifact_id, scope_id=scope, path=body.path, author=principal.id,
            expected_version=body.expected_version,
        )
    except ArtifactNotFound as exc:
        raise HTTPException(status_code=404, detail="not found") from exc
    except WorkspacePathEscape as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except CasConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except WorkspaceQuotaExceeded as exc:
        raise HTTPException(status_code=413, detail={"error_code": exc.code, "message": str(exc)}) from exc
    return SavedOut(artifact=_artifact_out(artifact), path=record.path, version=record.version, scope_id=scope)


@router.get("/{session_id}/runs/{turn_id}/inputs", response_model=list[RunInputOut])
async def run_inputs(
    session_id: str, turn_id: str, request: Request, tenant_id: str = Depends(deps.current_tenant)
) -> list[RunInputOut]:
    """What one run started from: its attachments and the workspace files, by version."""
    await _session(request, tenant_id, session_id)
    return [
        RunInputOut(kind=i.kind, scope_id=i.scope_id, path=i.path, version=i.version, content_sha256=i.content_sha256)
        for i in await _files(request).run_inputs(session_id, turn_id)
    ]
