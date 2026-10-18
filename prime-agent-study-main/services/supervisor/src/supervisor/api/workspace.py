"""Workspace HTTP API — durable SoT list/get/put/delete/presign (Stage 2 / D9).

Serves Postgres/object-store metadata + bytes — never sandbox host paths.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
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
    WorkspaceStore,
)

router = APIRouter(prefix="/v1/scopes", tags=["workspace"])


class WorkspaceFileOut(BaseModel):
    path: str
    content_sha256: str
    object_key: str
    size_bytes: int
    version: int
    mime_type: str | None = None
    file_id: str


class PresignDownloadOut(BaseModel):
    url: str
    method: str
    expires_in_s: int
    path: str
    content_sha256: str


class PutFileMeta(BaseModel):
    mime_type: str | None = None
    expected_version: int | None = Field(default=None)


def _workspace_store(request: Request) -> WorkspaceStore:
    store = getattr(deps._state(request), "workspace_store", None)  # noqa: SLF001
    if store is None:
        raise HTTPException(status_code=503, detail="workspace store not configured")
    return store


async def _require_scope_grant(
    request: Request,
    scope_id: str,
    grant: Grant,
    *,
    authorization: str | None,
    x_tenant_id: str | None,
    x_principal_id: str | None,
) -> Principal:
    """Grant check; miss → 404 (no scope/file enumeration)."""
    try:
        principal = await deps.current_principal(
            request,
            authorization=authorization,
            x_tenant_id=x_tenant_id,
            x_principal_id=x_principal_id,
        )
        authz = deps.get_authz(request)
        return await authz.require(principal, scope_id, grant)
    except UnauthorizedError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except (GrantDenied, NotScopeMember, ForbiddenError) as exc:
        raise HTTPException(status_code=404, detail="not found") from exc


@router.get("/{scope_id}/files", response_model=list[WorkspaceFileOut])
async def list_workspace_files(
    scope_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
    x_principal_id: str | None = Header(default=None),
) -> list[WorkspaceFileOut]:
    await _require_scope_grant(
        request,
        scope_id,
        Grant.WORKSPACE_READ,
        authorization=authorization,
        x_tenant_id=x_tenant_id,
        x_principal_id=x_principal_id,
    )
    store = _workspace_store(request)
    rows = await store.list_files(scope_id)
    return [
        WorkspaceFileOut(
            path=r.path,
            content_sha256=r.content_sha256,
            object_key=r.object_key,
            size_bytes=r.size_bytes,
            version=r.version,
            mime_type=r.mime_type,
            file_id=r.file_id,
        )
        for r in rows
    ]


@router.get("/{scope_id}/files/{path:path}")
async def get_workspace_file(
    scope_id: str,
    path: str,
    request: Request,
    authorization: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
    x_principal_id: str | None = Header(default=None),
    meta: bool = Query(default=False),
) -> Any:
    await _require_scope_grant(
        request,
        scope_id,
        Grant.WORKSPACE_READ,
        authorization=authorization,
        x_tenant_id=x_tenant_id,
        x_principal_id=x_principal_id,
    )
    store = _workspace_store(request)
    try:
        row = await store.stat(scope_id, path)
    except WorkspacePathEscape as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    if meta:
        return WorkspaceFileOut(
            path=row.path,
            content_sha256=row.content_sha256,
            object_key=row.object_key,
            size_bytes=row.size_bytes,
            version=row.version,
            mime_type=row.mime_type,
            file_id=row.file_id,
        )
    objects = getattr(store, "objects", None)
    if objects is None:
        raise HTTPException(status_code=503, detail="object storage not configured")
    data = await objects.get(row.object_key)
    media = row.mime_type or "application/octet-stream"
    return Response(content=data, media_type=media)


@router.put("/{scope_id}/files/{path:path}", response_model=WorkspaceFileOut)
async def put_workspace_file(
    scope_id: str,
    path: str,
    request: Request,
    authorization: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
    x_principal_id: str | None = Header(default=None),
    expected_version: int | None = Query(default=None),
    mime_type: str | None = Query(default=None),
) -> WorkspaceFileOut:
    await _require_scope_grant(
        request,
        scope_id,
        Grant.WORKSPACE_WRITE,
        authorization=authorization,
        x_tenant_id=x_tenant_id,
        x_principal_id=x_principal_id,
    )
    store = _workspace_store(request)
    body = await request.body()
    try:
        row = await store.commit_blob(
            scope_id,
            path,
            body,
            mime_type=mime_type,
            expected_version=expected_version,
        )
    except WorkspacePathEscape as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except CasConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return WorkspaceFileOut(
        path=row.path,
        content_sha256=row.content_sha256,
        object_key=row.object_key,
        size_bytes=row.size_bytes,
        version=row.version,
        mime_type=row.mime_type,
        file_id=row.file_id,
    )


@router.delete("/{scope_id}/files/{path:path}", status_code=204)
async def delete_workspace_file(
    scope_id: str,
    path: str,
    request: Request,
    authorization: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
    x_principal_id: str | None = Header(default=None),
) -> Response:
    await _require_scope_grant(
        request,
        scope_id,
        Grant.WORKSPACE_WRITE,
        authorization=authorization,
        x_tenant_id=x_tenant_id,
        x_principal_id=x_principal_id,
    )
    store = _workspace_store(request)
    try:
        await store.soft_delete(scope_id, path)
    except WorkspacePathEscape as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(status_code=204)


@router.post(
    "/{scope_id}/files/{path:path}/download",
    response_model=PresignDownloadOut,
)
async def presign_workspace_download(
    scope_id: str,
    path: str,
    request: Request,
    authorization: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
    x_principal_id: str | None = Header(default=None),
    ttl_s: int = Query(default=300, ge=1, le=86400),
) -> PresignDownloadOut:
    await _require_scope_grant(
        request,
        scope_id,
        Grant.ARTIFACT_DOWNLOAD,
        authorization=authorization,
        x_tenant_id=x_tenant_id,
        x_principal_id=x_principal_id,
    )
    store = _workspace_store(request)
    try:
        row = await store.stat(scope_id, path)
    except WorkspacePathEscape as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    objects = getattr(store, "objects", None)
    if objects is None:
        raise HTTPException(status_code=503, detail="object storage not configured")
    signed = await objects.create_download_url(row.object_key, ttl_s=ttl_s)
    return PresignDownloadOut(
        url=signed.url,
        method=signed.method,
        expires_in_s=signed.expires_in_s,
        path=row.path,
        content_sha256=row.content_sha256,
    )
