"""Agent workspace BFF — proxies durable supervisor SoT under grants (D9).

Browser never talks to supervisor or sandbox FS. Distinct from tenant Settings
"workspace" org chrome.
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prime_agent.config import Settings, get_settings
from prime_agent.db.models.session_link import SessionLink
from prime_agent.schemas.ai_identity import TenantPrincipal
from prime_agent.schemas.workspace import (
    EnsureScopeResponse,
    InviteScopeMemberIn,
    ScopeMemberOut,
    WorkspaceFileOut,
    WorkspaceRehydrateResponse,
    WorkspaceUploadStartIn,
    WorkspaceUploadStartOut,
)
from prime_agent.services.ai_client import AiClient, AiUpstreamError
from prime_agent.services.session_service import SessionNotFoundError


def raise_workspace_upstream_http(exc: AiUpstreamError) -> None:
    code = exc.status_code or 503
    if code < 400:
        code = 503
    raise HTTPException(
        status_code=code if code != 401 else 502,
        detail={
            "error_code": "AI_UPSTREAM_ERROR",
            "message": str(exc),
        },
    ) from exc


class WorkspaceService:
    """Durable agent-file proxy (scope grants enforced upstream)."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: AiClient | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._client = client or AiClient.from_settings(self._settings)

    @classmethod
    def from_settings(cls, settings: Settings) -> WorkspaceService:
        return cls(settings)

    async def ensure_scope(
        self,
        *,
        scope_id: str,
        identity: TenantPrincipal,
    ) -> EnsureScopeResponse:
        try:
            raw = await self._client.ensure_workspace_scope(identity, scope_id)
        except AiUpstreamError as exc:
            raise_workspace_upstream_http(exc)
        return EnsureScopeResponse(
            scope_id=str(raw.get("scope_id") or scope_id),
            status=str(raw.get("status") or "ok"),
        )

    async def list_files(
        self,
        *,
        scope_id: str,
        identity: TenantPrincipal,
    ) -> list[WorkspaceFileOut]:
        try:
            rows = await self._client.list_workspace_files(identity, scope_id)
        except AiUpstreamError as exc:
            raise_workspace_upstream_http(exc)
        return [WorkspaceFileOut.model_validate(r) for r in rows]

    async def get_file_bytes(
        self,
        *,
        scope_id: str,
        path: str,
        identity: TenantPrincipal,
    ) -> bytes:
        try:
            return await self._client.get_workspace_file_bytes(
                identity, scope_id, path
            )
        except AiUpstreamError as exc:
            raise_workspace_upstream_http(exc)

    async def put_file(
        self,
        *,
        scope_id: str,
        path: str,
        data: bytes,
        identity: TenantPrincipal,
        mime_type: str | None = None,
        expected_version: int | None = None,
    ) -> WorkspaceFileOut:
        try:
            raw = await self._client.put_workspace_file(
                identity,
                scope_id,
                path,
                data,
                mime_type=mime_type,
                expected_version=expected_version,
            )
        except AiUpstreamError as exc:
            raise_workspace_upstream_http(exc)
        return WorkspaceFileOut.model_validate(raw)

    async def start_upload(
        self, *, scope_id: str, body: WorkspaceUploadStartIn, identity: TenantPrincipal
    ) -> WorkspaceUploadStartOut:
        payload = body.model_dump(exclude_none=True)
        payload["content_sha256"] = body.content_sha256.lower()
        try:
            raw = await self._client.start_workspace_upload(identity, scope_id, payload)
        except AiUpstreamError as exc:
            raise_workspace_upstream_http(exc)
        return WorkspaceUploadStartOut.model_validate(raw)

    async def complete_upload(
        self, *, scope_id: str, upload_token: str, identity: TenantPrincipal
    ) -> WorkspaceFileOut:
        try:
            raw = await self._client.complete_workspace_upload(identity, scope_id, upload_token)
        except AiUpstreamError as exc:
            raise_workspace_upstream_http(exc)
        return WorkspaceFileOut.model_validate(raw)

    async def delete_file(
        self,
        *,
        scope_id: str,
        path: str,
        identity: TenantPrincipal,
    ) -> None:
        try:
            await self._client.delete_workspace_file(identity, scope_id, path)
        except AiUpstreamError as exc:
            raise_workspace_upstream_http(exc)

    async def rehydrate_session(
        self,
        *,
        session_id: str,
        user_id: str,
        identity: TenantPrincipal,
        db: AsyncSession,
    ) -> WorkspaceRehydrateResponse:
        link = await self._owned_session(session_id, user_id, db)
        if not link.scope_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error_code": "SESSION_HAS_NO_SCOPE",
                    "message": "session has no agent workspace scope_id",
                },
            )
        try:
            await self._client.rehydrate_session_workspace(
                identity, link.ai_session_id
            )
        except AiUpstreamError as exc:
            if exc.status_code == 409 and "no sandbox" in str(exc):
                # Built on first use, from the workspace as it is then: nothing to refresh.
                return WorkspaceRehydrateResponse(status="no_sandbox")
            raise_workspace_upstream_http(exc)
        return WorkspaceRehydrateResponse(status="ok")

    async def list_scope_members(
        self,
        *,
        scope_id: str,
        identity: TenantPrincipal,
    ) -> list[ScopeMemberOut]:
        try:
            rows = await self._client.list_scope_members(identity, scope_id)
        except AiUpstreamError as exc:
            raise_workspace_upstream_http(exc)
        return [ScopeMemberOut.model_validate(r) for r in rows]

    async def invite_scope_member(
        self,
        *,
        scope_id: str,
        identity: TenantPrincipal,
        body: InviteScopeMemberIn,
    ) -> ScopeMemberOut:
        try:
            raw = await self._client.invite_scope_member(
                identity,
                scope_id,
                principal_id=body.principal_id,
                grants=body.grants,
            )
        except AiUpstreamError as exc:
            raise_workspace_upstream_http(exc)
        return ScopeMemberOut.model_validate(raw)

    async def revoke_scope_member(
        self,
        *,
        scope_id: str,
        principal_id: str,
        identity: TenantPrincipal,
    ) -> None:
        try:
            await self._client.revoke_scope_member(identity, scope_id, principal_id)
        except AiUpstreamError as exc:
            raise_workspace_upstream_http(exc)

    async def _owned_session(
        self, session_id: str, user_id: str, db: AsyncSession
    ) -> SessionLink:
        result = await db.execute(
            select(SessionLink).where(
                SessionLink.id == session_id,
                SessionLink.user_id == user_id,
            )
        )
        link = result.scalar_one_or_none()
        if link is None:
            raise SessionNotFoundError()
        return link
