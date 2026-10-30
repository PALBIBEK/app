"""Session BFF service — ownership + D3 pins + D2 gates (D4 production)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from prime_agent.config import Settings, get_settings
from prime_agent.db.models.harness_release import HarnessRelease
from prime_agent.db.models.session_link import SessionLink
from prime_agent.schemas.ai_identity import TenantPrincipal
from prime_agent.schemas.session import (
    CreateSessionFromHarness,
    ForkSessionRequest,
    ForkSessionResponse,
    RunResponse,
    SessionApprovalChoice,
    SessionApprovalOut,
    SessionChildOut,
    SessionListResponse,
    SessionMessageRequest,
    SessionMessageResponse,
    SessionPinSnapshot,
    SessionPinsResponse,
    SessionResponse,
    SessionStartupResponse,
    SessionToolInvokeRequest,
    SessionToolInvokeResponse,
    SpawnSubagentRequest,
    SpawnSubagentResponse,
)
from prime_agent.services.ai_client import AiClient, AiUpstreamError
from prime_agent.services.harness_bind_service import (
    HarnessBindService,
)
from prime_agent.services.platform_status_service import (
    PlatformStatusService,
)

ERASING_AT_ONCE = 8
"""Sessions ended and erased at the same time when a harness is deleted."""


class SessionNotFoundError(Exception):
    """Owned session missing — map to 404."""

    def __init__(self, message: str = "Session not found") -> None:
        super().__init__(message)
        self.error_code = "SESSION_NOT_FOUND"

    def to_dict(self) -> dict[str, Any]:
        return {"error_code": self.error_code, "message": str(self)}


def raise_session_not_found_http(exc: SessionNotFoundError) -> None:
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=exc.to_dict(),
    ) from exc


class SessionService:
    """Product session lifecycle over supervisor."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: AiClient | None = None,
        bind: HarnessBindService | None = None,
        platform: PlatformStatusService | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._client = client or AiClient.from_settings(self._settings)
        self._bind = bind or HarnessBindService(
            self._settings, client=self._client
        )
        self._platform = platform or PlatformStatusService(
            self._settings, client=self._client
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> SessionService:
        return cls(settings)

    async def create_from_harness_id(
        self,
        *,
        request: CreateSessionFromHarness,
        user_id: str,
        identity: TenantPrincipal,
        db: AsyncSession,
    ) -> SessionResponse:
        pin = await self._bind.ensure_runnable_pin(
            harness_id=request.harness_id,
            user_id=user_id,
            identity=identity,
            db=db,
            require_run_allowed=True,
        )
        try:
            upstream = await self._client.create_session(
                identity,
                harness_version=pin.harness_version,
                runtime=pin.runtime,
                composition_id=pin.composition_id,
                composition_digest=pin.composition_digest,
                scope_id=request.scope_id,
            )
        except AiUpstreamError as exc:
            raise HTTPException(
                status_code=exc.status_code or 503,
                detail={
                    "error_code": "AI_UPSTREAM_ERROR",
                    "message": str(exc),
                },
            ) from exc

        ai_session_id = str(upstream.get("session_id") or "")
        if not ai_session_id:
            raise HTTPException(
                status_code=503,
                detail={
                    "error_code": "AI_UPSTREAM_ERROR",
                    "message": "supervisor did not return session_id",
                },
            )

        now = datetime.now(UTC)
        link = SessionLink(
            id=str(uuid4()),
            user_id=user_id,
            harness_id=request.harness_id,
            ai_session_id=ai_session_id,
            tenant_id=identity.tenant_id,
            scope_id=request.scope_id,
            harness_version=pin.harness_version,
            runtime=pin.runtime,
            composition_id=pin.composition_id,
            composition_digest=pin.composition_digest,
            status=str(upstream.get("status") or "active"),
            created_at=now,
            updated_at=now,
        )
        db.add(link)
        await db.commit()
        await db.refresh(link)
        return self._to_response(link)

    async def list_sessions(
        self,
        *,
        user_id: str,
        identity: TenantPrincipal,
        db: AsyncSession,
        page: int = 1,
        page_size: int = 20,
        harness_id: str | None = None,
    ) -> SessionListResponse:
        stmt = select(SessionLink).where(SessionLink.user_id == user_id)
        count_stmt = (
            select(func.count())
            .select_from(SessionLink)
            .where(SessionLink.user_id == user_id)
        )
        if harness_id:
            stmt = stmt.where(SessionLink.harness_id == harness_id)
            count_stmt = count_stmt.where(SessionLink.harness_id == harness_id)
        total = int((await db.execute(count_stmt)).scalar_one())
        stmt = (
            stmt.order_by(SessionLink.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        rows = list((await db.execute(stmt)).scalars().all())
        # Refresh cost/status from supervisor when possible
        refreshed: list[SessionResponse] = []
        for link in rows:
            refreshed.append(
                await self._maybe_refresh(link, identity=identity, db=db)
            )
        return SessionListResponse(
            data=refreshed,
            total=total,
            page=page,
            page_size=page_size,
        )

    async def get_session(
        self,
        *,
        session_id: str,
        user_id: str,
        identity: TenantPrincipal,
        db: AsyncSession,
    ) -> SessionResponse:
        link = await self._get_owned(session_id, user_id, db)
        return await self._maybe_refresh(link, identity=identity, db=db)

    async def _run_call(self, session_id: str, user_id: str, identity: TenantPrincipal, db: AsyncSession, call):  # noqa: ANN001
        link = await self._get_owned(session_id, user_id, db)
        try:
            return await call(link.ai_session_id)
        except AiUpstreamError as exc:
            raise HTTPException(
                status_code=exc.status_code or 503,
                detail={"error_code": "AI_UPSTREAM_ERROR", "message": str(exc)},
            ) from exc

    async def list_runs(self, *, session_id: str, user_id: str, identity: TenantPrincipal, db: AsyncSession, limit: int = 20) -> list[RunResponse]:
        rows = await self._run_call(session_id, user_id, identity, db, lambda sid: self._client.list_runs(identity, sid, limit=limit))
        return [RunResponse.model_validate(r) for r in rows]

    async def get_run(self, *, session_id: str, run_id: str, user_id: str, identity: TenantPrincipal, db: AsyncSession) -> RunResponse:
        raw = await self._run_call(session_id, user_id, identity, db, lambda sid: self._client.get_run(identity, sid, run_id))
        return RunResponse.model_validate(raw)

    async def cancel_run(self, *, session_id: str, run_id: str, user_id: str, identity: TenantPrincipal, db: AsyncSession) -> RunResponse:
        raw = await self._run_call(session_id, user_id, identity, db, lambda sid: self._client.cancel_run(identity, sid, run_id))
        return RunResponse.model_validate(raw)

    async def pins(
        self,
        *,
        session_id: str,
        user_id: str,
        identity: TenantPrincipal,
        db: AsyncSession,
    ) -> SessionPinsResponse:
        """What the session runs, part by part, with the agent version it was published as."""
        link = await self._get_owned(session_id, user_id, db)
        try:
            raw = await self._client.session_pins(identity, link.ai_session_id)
        except AiUpstreamError as exc:
            raise HTTPException(
                status_code=exc.status_code or 503,
                detail={"error_code": "AI_UPSTREAM_ERROR", "message": str(exc)},
            ) from exc
        release = (
            await db.execute(
                select(HarnessRelease.version).where(
                    HarnessRelease.harness_id == link.harness_id,
                    HarnessRelease.composition_digest == raw.get("composition_digest"),
                )
            )
        ).scalar_one_or_none()
        return SessionPinsResponse.model_validate({**raw, "release_version": release})

    async def startup(
        self,
        *,
        session_id: str,
        user_id: str,
        identity: TenantPrincipal,
        db: AsyncSession,
    ) -> SessionStartupResponse:
        """Where the session's start stands; a session that started or failed is recorded so."""
        link = await self._get_owned(session_id, user_id, db)
        try:
            raw = await self._client.session_startup(identity, link.ai_session_id)
        except AiUpstreamError as exc:
            raise HTTPException(
                status_code=exc.status_code or 503,
                detail={"error_code": "AI_UPSTREAM_ERROR", "message": str(exc)},
            ) from exc
        out = SessionStartupResponse.model_validate(
            {k: raw.get(k) for k in ("status", "worker_available", "reason", "kind")}
        )
        new = {"ready": "active", "failed": "failed"}.get(out.status)
        if new is not None and link.status != new and link.status == "starting":
            link.status = new
            link.updated_at = datetime.now(UTC)
            await db.commit()
        return out

    async def post_message(
        self,
        *,
        session_id: str,
        user_id: str,
        identity: TenantPrincipal,
        body: SessionMessageRequest,
        db: AsyncSession,
    ) -> SessionMessageResponse:
        link = await self._get_owned(session_id, user_id, db)
        await self._platform.ensure_ready(identity)
        command_id = body.command_id or str(uuid4())
        try:
            upstream = await self._client.post_message(
                identity,
                link.ai_session_id,
                text=body.content,
                command_id=command_id,
                steer=body.steer,
            )
        except AiUpstreamError as exc:
            code = "AI_UPSTREAM_ERROR"
            if exc.status_code == 409:
                code = "SESSION_AWAITING_APPROVAL"
            elif exc.status_code == 429:
                code = "BUDGET_EXCEEDED"
            raise HTTPException(
                status_code=exc.status_code or 503,
                detail={
                    "error_code": code,
                    "message": str(exc),
                },
            ) from exc
        link.message_count = int(link.message_count or 0) + 1
        link.updated_at = datetime.now(UTC)
        await db.commit()
        return SessionMessageResponse(
            turn_id=str(upstream.get("turn_id") or ""),
            command_id=command_id,
            status=str(upstream.get("status") or "accepted"),
            queued=upstream.get("queued") is True,
            steered=upstream.get("steered") is True,
            held=upstream.get("held") is True,
        )

    async def invoke_tool(
        self,
        *,
        session_id: str,
        user_id: str,
        identity: TenantPrincipal,
        body: SessionToolInvokeRequest,
        db: AsyncSession,
    ) -> SessionToolInvokeResponse:
        """Owner-scoped tools/invoke with D2 run_allowed gate (D6 F/G)."""
        link = await self._get_owned(session_id, user_id, db)
        await self._platform.ensure_ready(identity)
        try:
            upstream = await self._client.invoke_tool(
                identity,
                link.ai_session_id,
                name=body.name,
                arguments=body.arguments or {},
                tool_call_id=body.tool_call_id,
            )
        except AiUpstreamError as exc:
            code = "AI_UPSTREAM_ERROR"
            if exc.status_code == 409:
                code = "SESSION_AWAITING_APPROVAL"
            raise HTTPException(
                status_code=exc.status_code or 503,
                detail={
                    "error_code": code,
                    "message": str(exc),
                },
            ) from exc
        return SessionToolInvokeResponse(
            tool_call_id=str(upstream.get("tool_call_id") or body.tool_call_id or ""),
            ok=bool(upstream.get("ok", False)),
            payload=upstream.get("payload"),
            error=upstream.get("error") if isinstance(upstream.get("error"), str) else None,
            code=upstream.get("code") if isinstance(upstream.get("code"), str) else None,
        )

    async def list_approvals(
        self,
        *,
        session_id: str,
        user_id: str,
        identity: TenantPrincipal,
        db: AsyncSession,
    ) -> list[SessionApprovalOut]:
        """Owner-scoped pending approvals via AiClient — never expose supervisor URL."""
        link = await self._get_owned(session_id, user_id, db)
        try:
            rows = await self._client.list_session_approvals(identity, link.ai_session_id)
        except AiUpstreamError as exc:
            raise HTTPException(
                status_code=exc.status_code or 503,
                detail={
                    "error_code": "AI_UPSTREAM_ERROR",
                    "message": str(exc),
                },
            ) from exc
        out: list[SessionApprovalOut] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            out.append(
                SessionApprovalOut(
                    approval_id=str(row.get("approval_id") or ""),
                    session_id=session_id,
                    prompt=str(row.get("prompt") or ""),
                    options=[str(o) for o in (row.get("options") or [])],
                    status=str(row.get("status") or ""),
                    choice=str(row["choice"]) if row.get("choice") is not None else None,
                    timeout_s=float(row.get("timeout_s") or 0),
                )
            )
        return out

    async def respond_approval(
        self,
        *,
        session_id: str,
        approval_id: str,
        user_id: str,
        identity: TenantPrincipal,
        body: SessionApprovalChoice,
        db: AsyncSession,
    ) -> SessionApprovalOut:
        """Respond to an approval for an owned session (D6 E via BFF)."""
        await self._get_owned(session_id, user_id, db)
        try:
            upstream = await self._client.respond_approval(
                identity,
                approval_id,
                choice=body.choice,
            )
        except AiUpstreamError as exc:
            code = "AI_UPSTREAM_ERROR"
            if exc.status_code == 422:
                code = "INVALID_APPROVAL_CHOICE"
            raise HTTPException(
                status_code=exc.status_code or 503,
                detail={
                    "error_code": code,
                    "message": str(exc),
                },
            ) from exc
        return SessionApprovalOut(
            approval_id=str(upstream.get("approval_id") or approval_id),
            session_id=session_id,
            prompt=str(upstream.get("prompt") or ""),
            options=[str(o) for o in (upstream.get("options") or [])],
            status=str(upstream.get("status") or ""),
            choice=str(upstream["choice"]) if upstream.get("choice") is not None else None,
            timeout_s=float(upstream.get("timeout_s") or 0),
        )

    async def stream_events(
        self,
        *,
        session_id: str,
        user_id: str,
        identity: TenantPrincipal,
        last_event_id: str | None,
        db: AsyncSession,
    ):
        link = await self._get_owned(session_id, user_id, db)
        try:
            return await self._client.stream_session_events(
                identity,
                link.ai_session_id,
                last_event_id=last_event_id,
            )
        except AiUpstreamError as exc:
            raise HTTPException(
                status_code=exc.status_code or 503,
                detail={
                    "error_code": "AI_UPSTREAM_ERROR",
                    "message": str(exc),
                },
            ) from exc

    async def abort(
        self,
        *,
        session_id: str,
        user_id: str,
        identity: TenantPrincipal,
        db: AsyncSession,
    ) -> dict[str, str]:
        link = await self._get_owned(session_id, user_id, db)
        try:
            await self._client.abort_session(identity, link.ai_session_id)
        except AiUpstreamError as exc:
            raise HTTPException(
                status_code=exc.status_code or 503,
                detail={
                    "error_code": "AI_UPSTREAM_ERROR",
                    "message": str(exc),
                },
            ) from exc
        return {"status": "ok"}

    async def terminate(
        self,
        *,
        session_id: str,
        user_id: str,
        identity: TenantPrincipal,
        db: AsyncSession,
    ) -> None:
        link = await self._get_owned(session_id, user_id, db)
        try:
            await self._client.terminate_session(identity, link.ai_session_id)
        except AiUpstreamError as exc:
            raise HTTPException(
                status_code=exc.status_code or 503,
                detail={
                    "error_code": "AI_UPSTREAM_ERROR",
                    "message": str(exc),
                },
            ) from exc
        link.status = "terminated"
        link.updated_at = datetime.now(UTC)
        await db.commit()


    async def erase(
        self,
        *,
        session_id: str,
        user_id: str,
        identity: TenantPrincipal,
        db: AsyncSession,
    ) -> None:
        """Erase the session: the supervisor's record of it (journal, files, checkpoints),
        then the product's link to it. Safe to repeat: one already erased upstream is done."""
        link = await self._get_owned(session_id, user_id, db)
        await self._erase_upstream(identity, link.ai_session_id)
        await db.delete(link)
        await db.commit()

    async def erase_harness_sessions(
        self,
        *,
        harness_id: str,
        user_id: str,
        identity: TenantPrincipal,
        db: AsyncSession,
    ) -> int:
        """Erase every session of a harness (before the harness is deleted, which would
        otherwise drop their links and leave them in the supervisor, out of reach).

        Ended and erased upstream a few at a time; a link goes once its session is gone, so
        a failure part way keeps what is left reachable, and erasing again finishes the job.
        Returns the sessions erased."""
        links = list(
            (
                await db.execute(
                    select(SessionLink).where(SessionLink.harness_id == harness_id, SessionLink.user_id == user_id)
                )
            ).scalars()
        )
        if not links or not self._client.configured:  # without a supervisor, nothing is kept there
            return 0
        at_once = asyncio.Semaphore(ERASING_AT_ONCE)

        async def erase(link: SessionLink) -> SessionLink | HTTPException:
            async with at_once:
                try:
                    await self._erase_upstream(identity, link.ai_session_id)
                except HTTPException as exc:
                    return exc
                return link

        outcomes = await asyncio.gather(*(erase(link) for link in links))
        for outcome in outcomes:
            if isinstance(outcome, SessionLink):
                await db.delete(outcome)
        await db.commit()
        if failed := [o for o in outcomes if isinstance(o, HTTPException)]:
            raise failed[0]
        return len(links)

    async def _erase_upstream(self, identity: TenantPrincipal, ai_session_id: str) -> None:
        """End and erase the supervisor's session (already erased counts as done)."""
        try:
            await self._client.erase_session(identity, ai_session_id)
        except AiUpstreamError as exc:
            if exc.status_code != 404:
                raise HTTPException(
                    status_code=exc.status_code or 503,
                    detail={"error_code": "AI_UPSTREAM_ERROR", "message": str(exc)},
                ) from exc

    @staticmethod
    def _product_subagent_provider(provider: str | None) -> str:
        """Surface echo stub as lab_echo — never as long-term memory."""
        raw = (provider or "").strip() or "lab_echo"
        if raw == "memory":
            return "lab_echo"
        return raw

    async def list_children(
        self,
        *,
        session_id: str,
        user_id: str,
        identity: TenantPrincipal,
        db: AsyncSession,
        scope: str = "direct",
    ) -> list[SessionChildOut]:
        """Owner-scoped children via AiClient — never expose supervisor URL/token."""
        link = await self._get_owned(session_id, user_id, db)
        try:
            rows = await self._client.list_session_children(
                identity, link.ai_session_id, scope=scope
            )
        except AiUpstreamError as exc:
            raise HTTPException(
                status_code=exc.status_code or 503,
                detail={
                    "error_code": "AI_UPSTREAM_ERROR",
                    "message": str(exc),
                },
            ) from exc
        out: list[SessionChildOut] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            out.append(
                SessionChildOut(
                    id=str(row.get("id") or ""),
                    mode=str(row.get("mode") or "spawn"),
                    provider=self._product_subagent_provider(
                        str(row.get("provider") or "")
                    ),
                    parent_id=str(row.get("parent_id") or link.ai_session_id),
                    run_id=str(row.get("run_id") or ""),
                    label=str(row["label"]) if row.get("label") is not None else None,
                    activity=str(row.get("activity") or "running"),
                    depth=int(row["depth"]) if row.get("depth") is not None else None,
                    session_id=session_id,
                )
            )
        return out

    async def spawn_subagent(
        self,
        *,
        session_id: str,
        user_id: str,
        identity: TenantPrincipal,
        body: SpawnSubagentRequest,
        db: AsyncSession,
    ) -> SpawnSubagentResponse:
        """Owner-scoped spawn via AiClient (D12 B). Product path is one-shot only."""
        mode = (body.mode or "spawn").strip().lower()
        if mode not in {"spawn", "one_shot", "one-shot"}:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error_code": "SUBAGENT_MODE_NOT_WIRED",
                    "message": f"mode {body.mode!r} is not wired on BFF; use spawn",
                },
            )
        link = await self._get_owned(session_id, user_id, db)
        await self._platform.ensure_ready(identity)
        try:
            upstream = await self._client.spawn_subagent(
                identity,
                link.ai_session_id,
                prompt=body.prompt,
                description=body.description,
                provider=body.provider,
                mode="spawn",
                parent_depth=body.parent_depth,
                run_id=body.run_id,
                tool_filter=body.tool_filter,
                persona=body.persona,
                depth_limit=body.depth_limit,
                output_schema=body.output_schema,
            )
        except AiUpstreamError as exc:
            raise HTTPException(
                status_code=exc.status_code or 503,
                detail={
                    "error_code": "AI_UPSTREAM_ERROR",
                    "message": str(exc),
                },
            ) from exc
        child_id = str(
            upstream.get("child_session_id")
            or upstream.get("id")
            or upstream.get("child_id")
            or ""
        )
        return SpawnSubagentResponse(
            child_session_id=child_id,
            text=str(upstream["text"]) if upstream.get("text") is not None else None,
            status=str(upstream["status"]) if upstream.get("status") is not None else None,
            run_id=str(upstream["run_id"]) if upstream.get("run_id") is not None else None,
            provider=self._product_subagent_provider(body.provider),
        )

    async def fork(
        self,
        *,
        session_id: str,
        user_id: str,
        identity: TenantPrincipal,
        body: ForkSessionRequest,
        db: AsyncSession,
    ) -> ForkSessionResponse:
        parent = await self._get_owned(session_id, user_id, db)
        await self._platform.ensure_ready(identity)
        try:
            upstream = await self._client.fork_session(
                identity,
                parent.ai_session_id,
                at_seq=body.at_seq,
            )
        except AiUpstreamError as exc:
            raise HTTPException(
                status_code=exc.status_code or 503,
                detail={
                    "error_code": "AI_UPSTREAM_ERROR",
                    "message": str(exc),
                },
            ) from exc
        new_ai_id = str(upstream.get("session_id") or "")
        if not new_ai_id:
            raise HTTPException(
                status_code=503,
                detail={
                    "error_code": "AI_UPSTREAM_ERROR",
                    "message": "supervisor did not return forked session_id",
                },
            )
        now = datetime.now(UTC)
        child = SessionLink(
            id=str(uuid4()),
            user_id=user_id,
            harness_id=parent.harness_id,
            ai_session_id=new_ai_id,
            tenant_id=parent.tenant_id,
            scope_id=parent.scope_id,
            harness_version=parent.harness_version,
            runtime=parent.runtime,
            composition_id=parent.composition_id,
            composition_digest=parent.composition_digest,
            status="active",
            created_at=now,
            updated_at=now,
        )
        db.add(child)
        await db.commit()
        return ForkSessionResponse(id=child.id, parent_id=parent.id)

    async def _get_owned(
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

    async def _maybe_refresh(
        self,
        link: SessionLink,
        *,
        identity: TenantPrincipal,
        db: AsyncSession,
    ) -> SessionResponse:
        try:
            detail = await self._client.get_session(identity, link.ai_session_id)
        except AiUpstreamError:
            return self._to_response(link)
        if isinstance(detail, dict):
            if "status" in detail:
                link.status = str(detail["status"])
            if "message_count" in detail:
                link.message_count = int(detail["message_count"] or 0)
            if "total_cost" in detail:
                link.total_cost = float(detail["total_cost"] or 0.0)
            if "last_seq" in detail:
                link.last_seq = int(detail["last_seq"] or 0)
            if detail.get("title") is not None:
                link.title = str(detail["title"])
            link.updated_at = datetime.now(UTC)
            await db.commit()
            return self._to_response(
                link,
                is_busy=detail.get("is_busy")
                if isinstance(detail.get("is_busy"), bool)
                else None,
            )
        return self._to_response(link)

    def _to_response(
        self, link: SessionLink, *, is_busy: bool | None = None
    ) -> SessionResponse:
        return SessionResponse(
            id=link.id,
            harness_id=link.harness_id,
            status=link.status,
            harness_version=link.harness_version,
            runtime=link.runtime,
            composition_id=link.composition_id,
            composition_digest=link.composition_digest,
            scope_id=link.scope_id,
            title=link.title,
            message_count=link.message_count,
            total_cost=float(link.total_cost or 0.0),
            last_seq=int(link.last_seq or 0),
            is_busy=is_busy,
            created_at=link.created_at,
            updated_at=link.updated_at,
            pin=SessionPinSnapshot(
                harness_version=link.harness_version,
                runtime=link.runtime,
                composition_id=link.composition_id,
                composition_digest=link.composition_digest,
            ),
        )
