"""Workspace HTTP API — durable SoT list/get/put/delete/presign (Stage 2 / D9).

Serves Postgres/object-store metadata + bytes — never sandbox host paths.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from supervisor import workspace_uploads as uploads
from supervisor.api import deps
from supervisor.seams.providers.workspace import normalize_workspace_path
from supervisor.seams.services import (
    CasConflict,
    DirectUploadUnsupported,
    ForbiddenError,
    Grant,
    GrantDenied,
    NotScopeMember,
    ObjectIntegrityError,
    ObjectNotFound,
    Principal,
    UnauthorizedError,
    WorkspacePathEscape,
    WorkspaceStore,
)

logger = logging.getLogger(__name__)

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


class UploadStartIn(BaseModel):
    path: str = Field(min_length=1, max_length=1024)
    size_bytes: int = Field(ge=0)
    content_sha256: str = Field(description="Lowercase hex SHA-256 of the whole file")
    mime_type: str | None = Field(default=None, max_length=255)
    expected_version: int | None = Field(default=None, ge=0)


class UploadStartOut(BaseModel):
    """``upload``: PUT the bytes to ``url`` with ``headers``, then complete with ``upload_token``.
    ``committed``: the store already had these bytes; ``file`` is recorded, nothing to send.
    ``proxy``: this store cannot take direct uploads; PUT the bytes to the files route."""

    status: Literal["upload", "committed", "proxy"]
    max_bytes: int
    upload_token: str | None = None
    url: str | None = None
    method: str | None = None
    headers: dict[str, str] | None = None
    expires_in_s: int | None = None
    file: WorkspaceFileOut | None = None


class UploadCompleteIn(BaseModel):
    upload_token: str = Field(min_length=1, max_length=4096)


def _file_out(row: Any) -> WorkspaceFileOut:
    return WorkspaceFileOut(
        path=row.path,
        content_sha256=row.content_sha256,
        object_key=row.object_key,
        size_bytes=row.size_bytes,
        version=row.version,
        mime_type=row.mime_type,
        file_id=row.file_id,
    )


def _too_large(size: int, limit: int) -> HTTPException:
    return HTTPException(
        status_code=413,
        detail=f"file is {size} bytes; this workspace takes files up to {limit} bytes",
    )


def _workspace_store(request: Request) -> WorkspaceStore:
    store = getattr(deps._state(request), "workspace_store", None)
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
    limit = uploads.max_upload_bytes()
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise _too_large(int(declared), limit)
    body = await request.body()
    if len(body) > limit:
        raise _too_large(len(body), limit)
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


@router.post("/{scope_id}/uploads", response_model=UploadStartOut)
async def start_workspace_upload(
    scope_id: str,
    body: UploadStartIn,
    request: Request,
    authorization: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
    x_principal_id: str | None = Header(default=None),
) -> UploadStartOut:
    """Step 1 of a direct upload (see supervisor.workspace_uploads)."""
    await _require_scope_grant(
        request,
        scope_id,
        Grant.WORKSPACE_WRITE,
        authorization=authorization,
        x_tenant_id=x_tenant_id,
        x_principal_id=x_principal_id,
    )
    store = _workspace_store(request)
    limit = uploads.max_upload_bytes()
    if body.size_bytes > limit:
        raise _too_large(body.size_bytes, limit)
    sha = body.content_sha256.strip().lower()
    if not uploads.SHA256_HEX.match(sha):
        raise HTTPException(status_code=422, detail="content_sha256 must be 64 hex characters")
    try:
        path = normalize_workspace_path(body.path)
        live = await store.stat(scope_id, path)
    except WorkspacePathEscape as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    actual = 0 if live is None else live.version
    if body.expected_version is not None and actual != body.expected_version:
        raise HTTPException(
            status_code=409,
            detail=f"{path} is at version {actual}, not {body.expected_version}",
        )
    mime = body.mime_type or "application/octet-stream"
    objects = getattr(store, "objects", None)
    if objects is None:
        raise HTTPException(status_code=503, detail="object storage not configured")

    # The same bytes are already stored (another file, scope or earlier upload): just record.
    head = await objects.head(uploads.blob_key(sha))
    if head.exists and head.content_sha256 == sha and head.size_bytes == body.size_bytes:
        try:
            row = await store.commit_object(
                scope_id, path, content_sha256=sha, size_bytes=body.size_bytes,
                mime_type=mime, expected_version=body.expected_version,
            )
        except CasConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return UploadStartOut(status="committed", max_bytes=limit, file=_file_out(row))

    ttl = uploads.url_ttl_s()
    ticket = uploads.new_ticket(
        scope_id=scope_id, path=path, content_sha256=sha, size_bytes=body.size_bytes,
        mime_type=mime, expected_version=body.expected_version, ttl_s=ttl,
    )
    try:
        signed = await objects.create_upload_url(
            ticket.staging_key, size_bytes=body.size_bytes, content_sha256=sha,
            content_type=mime, ttl_s=ttl,
        )
    except DirectUploadUnsupported:
        return UploadStartOut(status="proxy", max_bytes=limit)
    return UploadStartOut(
        status="upload",
        max_bytes=limit,
        upload_token=uploads.sign(ticket),
        url=signed.url,
        method=signed.method,
        headers=signed.headers,
        expires_in_s=signed.expires_in_s,
    )


@router.post("/{scope_id}/uploads/complete", response_model=WorkspaceFileOut)
async def complete_workspace_upload(
    scope_id: str,
    body: UploadCompleteIn,
    request: Request,
    authorization: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
    x_principal_id: str | None = Header(default=None),
) -> WorkspaceFileOut:
    """Step 3: check what was uploaded, promote it to its content-addressed blob, record it."""
    await _require_scope_grant(
        request,
        scope_id,
        Grant.WORKSPACE_WRITE,
        authorization=authorization,
        x_tenant_id=x_tenant_id,
        x_principal_id=x_principal_id,
    )
    try:
        ticket = uploads.verify(body.upload_token, scope_id=scope_id)
    except uploads.TicketInvalid as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    store = _workspace_store(request)
    objects = getattr(store, "objects", None)
    if objects is None:
        raise HTTPException(status_code=503, detail="object storage not configured")

    staged = ticket.staging_key
    try:
        measured = await objects.digest(staged, max_bytes=ticket.size_bytes)
    except ObjectNotFound as exc:
        raise HTTPException(
            status_code=409, detail="nothing was uploaded for this ticket; send the file first"
        ) from exc
    except ObjectIntegrityError as exc:
        await objects.delete(staged)
        raise HTTPException(status_code=422, detail="the uploaded file is larger than announced") from exc
    if (measured.content_sha256, measured.size_bytes) != (ticket.content_sha256, ticket.size_bytes):
        await objects.delete(staged)
        logger.warning("upload %s in %s did not match what was announced", ticket.upload_id, scope_id)
        raise HTTPException(
            status_code=422,
            detail="the uploaded bytes do not match the announced size and SHA-256; upload again",
        )

    final = uploads.blob_key(ticket.content_sha256)
    if not await objects.exists(final):
        await objects.copy(staged, final, content_sha256=ticket.content_sha256)
    await objects.delete(staged)
    try:
        row = await store.commit_object(
            scope_id,
            ticket.path,
            content_sha256=ticket.content_sha256,
            size_bytes=ticket.size_bytes,
            mime_type=ticket.mime_type,
            expected_version=ticket.expected_version,
        )
    except CasConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except WorkspacePathEscape as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _file_out(row)
