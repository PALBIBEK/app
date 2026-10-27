"""Run traces through the BFF: the supervisor builds them; this names them for the user.

The supervisor knows its own session ids and composition digests. A user knows their
sessions, their agents and each agent's version numbers, so every run is mapped to the
user's session link, the agent it belongs to and the release its digest was published as.
Runs of sessions the user has no link to are left out.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prime_agent.config import Settings, get_settings
from prime_agent.db.models.harness import Harness
from prime_agent.db.models.harness_release import HarnessRelease
from prime_agent.db.models.session_link import SessionLink
from prime_agent.schemas.ai_identity import TenantPrincipal
from prime_agent.schemas.trace import (
    RunSummary,
    RunTraceResponse,
    SessionTracesResponse,
    TraceListResponse,
)
from prime_agent.services.ai_client import AiClient, AiUpstreamError

__all__ = ["TraceNotFoundError", "TraceService"]


class TraceNotFoundError(Exception):
    """No such session, run or span for this user (or not theirs: the same answer)."""


def _name_people(span: dict[str, Any], user_id: str) -> dict[str, Any]:
    """``span`` with the principal who answered an approval named as the user sees it."""
    attrs = span.get("attrs")
    if isinstance(attrs, dict) and attrs.get("responded_by") == user_id:
        return {**span, "attrs": {**attrs, "responded_by": "you"}}
    return span


def _upstream(exc: AiUpstreamError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code or 503, detail={"error_code": "AI_UPSTREAM_ERROR", "message": str(exc)}
    )


class TraceService:
    def __init__(self, settings: Settings, *, client: AiClient | None = None) -> None:
        self._client = client or AiClient.from_settings(settings)

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> TraceService:
        return cls(settings or get_settings())

    # --- naming ---------------------------------------------------------------

    @staticmethod
    async def _names(db: AsyncSession, links: list[SessionLink]) -> tuple[dict[str, str], dict[tuple[str, str], int]]:
        """Agent names by harness id, and release numbers by (harness id, composition digest)."""
        harness_ids = {link.harness_id for link in links}
        if not harness_ids:
            return {}, {}
        names = dict(
            (await db.execute(select(Harness.id, Harness.name).where(Harness.id.in_(harness_ids)))).tuples().all()
        )
        releases = {
            (hid, digest): version
            for hid, digest, version in (
                await db.execute(
                    select(HarnessRelease.harness_id, HarnessRelease.composition_digest, HarnessRelease.version).where(
                        HarnessRelease.harness_id.in_(harness_ids)
                    )
                )
            ).tuples()
            if digest
        }
        return names, releases

    @staticmethod
    def _summary(
        raw: dict[str, Any], link: SessionLink, names: dict[str, str], releases: dict[tuple[str, str], int]
    ) -> RunSummary:
        digest = raw.get("composition_digest") or link.composition_digest
        return RunSummary.model_validate(
            {
                **raw,
                "session_id": link.id,
                "agent_id": link.harness_id,
                "agent_name": names.get(link.harness_id),
                "version": releases.get((link.harness_id, digest or "")),
                "session_title": link.title,
            }
        )

    async def _link(self, db: AsyncSession, user_id: str, session_id: str) -> SessionLink:
        link = (
            await db.execute(select(SessionLink).where(SessionLink.id == session_id, SessionLink.user_id == user_id))
        ).scalar_one_or_none()
        if link is None:
            raise TraceNotFoundError()
        return link

    # --- reads ------------------------------------------------------------------

    async def list_runs(
        self,
        *,
        identity: TenantPrincipal,
        user_id: str,
        db: AsyncSession,
        statuses: list[str],
        agent_id: str | None,
        errors: bool,
        denied: bool,
        q: str | None,
        since_ms: int | None,
        before_ms: int | None,
        limit: int,
    ) -> TraceListResponse:
        params: dict[str, Any] = {"limit": str(limit)}
        if statuses:
            params["status"] = statuses
        if agent_id:
            harness = (
                await db.execute(select(Harness).where(Harness.id == agent_id, Harness.user_id == user_id))
            ).scalar_one_or_none()
            if harness is None or not harness.composition_id:
                return TraceListResponse(items=[], next_before_ms=None)  # not theirs, or never published
            params["composition_id"] = harness.composition_id
        if errors:
            params["errors"] = "true"
        if denied:
            params["denied"] = "true"
        if q:
            params["q"] = q
        if since_ms is not None:
            params["since_ms"] = str(since_ms)
        if before_ms is not None:
            params["before_ms"] = str(before_ms)
        try:
            raw = await self._client.list_traces(identity, params)
        except AiUpstreamError as exc:
            raise _upstream(exc) from exc
        items = [i for i in raw.get("items", []) if isinstance(i, dict)]
        ai_ids = {str(i.get("session_id")) for i in items}
        links = (
            (
                await db.execute(
                    select(SessionLink).where(SessionLink.user_id == user_id, SessionLink.ai_session_id.in_(ai_ids))
                )
            ).scalars().all()
            if ai_ids
            else []
        )
        by_ai = {link.ai_session_id: link for link in links}
        names, releases = await self._names(db, list(links))
        out = [
            self._summary(i, by_ai[str(i["session_id"])], names, releases)
            for i in items
            if str(i.get("session_id")) in by_ai
        ]
        return TraceListResponse(items=out, next_before_ms=raw.get("next_before_ms"))

    async def session_runs(
        self, *, identity: TenantPrincipal, user_id: str, session_id: str, db: AsyncSession
    ) -> SessionTracesResponse:
        link = await self._link(db, user_id, session_id)
        try:
            raw = await self._client.session_traces(identity, link.ai_session_id)
        except AiUpstreamError as exc:
            if exc.status_code == 404:
                raise TraceNotFoundError() from exc
            raise _upstream(exc) from exc
        names, releases = await self._names(db, [link])
        items = [self._summary(i, link, names, releases) for i in raw.get("items", []) if isinstance(i, dict)]
        return SessionTracesResponse(
            session_id=link.id,
            title=link.title,
            status=str(raw.get("status") or link.status),
            agent_id=link.harness_id,
            agent_name=names.get(link.harness_id),
            version=releases.get((link.harness_id, link.composition_digest or "")),
            items=items,
        )

    async def run(
        self, *, identity: TenantPrincipal, user_id: str, session_id: str, run_id: str, db: AsyncSession
    ) -> RunTraceResponse:
        link = await self._link(db, user_id, session_id)
        try:
            raw = await self._client.run_trace(identity, link.ai_session_id, run_id)
        except AiUpstreamError as exc:
            if exc.status_code == 404:
                raise TraceNotFoundError() from exc
            raise _upstream(exc) from exc
        names, releases = await self._names(db, [link])
        digest = raw.get("composition_digest") or link.composition_digest
        return RunTraceResponse(
            session_id=link.id,
            run_id=run_id,
            trace_id=str(raw.get("trace_id", "")),
            agent_id=link.harness_id,
            agent_name=names.get(link.harness_id),
            version=releases.get((link.harness_id, digest or "")),
            composition_digest=digest,
            session_title=link.title,
            rollup=raw.get("rollup") or {},
            spans=[_name_people(s, user_id) for s in raw.get("spans", []) if isinstance(s, dict)],
        )

    async def span(
        self,
        *,
        identity: TenantPrincipal,
        user_id: str,
        session_id: str,
        run_id: str,
        span_id: str,
        db: AsyncSession,
    ) -> dict[str, Any]:
        link = await self._link(db, user_id, session_id)
        try:
            return _name_people(await self._client.trace_span(identity, link.ai_session_id, run_id, span_id), user_id)
        except AiUpstreamError as exc:
            if exc.status_code == 404:
                raise TraceNotFoundError() from exc
            raise _upstream(exc) from exc

    async def events(
        self, *, identity: TenantPrincipal, user_id: str, session_id: str, run_id: str, db: AsyncSession
    ) -> dict[str, Any]:
        link = await self._link(db, user_id, session_id)
        try:
            raw = await self._client.run_events(identity, link.ai_session_id, run_id)
        except AiUpstreamError as exc:
            if exc.status_code == 404:
                raise TraceNotFoundError() from exc
            raise _upstream(exc) from exc
        return {"session_id": link.id, "run_id": run_id, "events": raw.get("events", [])}
