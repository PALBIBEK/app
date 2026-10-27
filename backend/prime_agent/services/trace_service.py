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
    AgentQualityResponse,
    FeedbackRequest,
    Issue,
    IssuesResponse,
    Judge,
    JudgeRequest,
    JudgesResponse,
    RunSummary,
    RunTraceResponse,
    SessionTracesResponse,
    TraceListResponse,
    TraceScore,
    VersionQuality,
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


def _score(raw: dict[str, Any], user_id: str) -> TraceScore:
    return TraceScore.model_validate({**raw, "mine": raw.get("principal_id") == user_id})


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
            (await db.execute(select(Harness.id, Harness.name).where(Harness.id.in_(harness_ids)))).all()
        )
        releases = {
            (hid, digest): version
            for hid, digest, version in (
                await db.execute(
                    select(HarnessRelease.harness_id, HarnessRelease.composition_digest, HarnessRelease.version).where(
                        HarnessRelease.harness_id.in_(harness_ids)
                    )
                )
            )
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
        failing_checks: bool,
        feedback: str | None,
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
        if failing_checks:
            params["failing_checks"] = "true"
        if feedback:
            params["feedback"] = feedback
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
            summary=raw.get("summary"),
            rollup=raw.get("rollup") or {},
            spans=[_name_people(s, user_id) for s in raw.get("spans", []) if isinstance(s, dict)],
            scores=[_score(x, user_id) for x in raw.get("scores", []) if isinstance(x, dict)],
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

    async def feedback(
        self,
        *,
        identity: TenantPrincipal,
        user_id: str,
        session_id: str,
        run_id: str,
        body: FeedbackRequest | None,
        span_id: str | None,
        db: AsyncSession,
    ) -> TraceScore | None:
        """Set the user's verdict (``body``), or take it back (``body`` None)."""
        link = await self._link(db, user_id, session_id)
        try:
            if body is None:
                await self._client.delete_feedback(identity, link.ai_session_id, run_id, span_id)
                return None
            raw = await self._client.put_feedback(identity, link.ai_session_id, run_id, body.model_dump())
        except AiUpstreamError as exc:
            if exc.status_code == 404:
                raise TraceNotFoundError() from exc
            raise _upstream(exc) from exc
        return _score(raw, user_id)

    async def quality(
        self, *, identity: TenantPrincipal, user_id: str, agent_id: str, db: AsyncSession
    ) -> AgentQualityResponse:
        """How each version of the user's agent did, numbered as its releases."""
        harness = (
            await db.execute(select(Harness).where(Harness.id == agent_id, Harness.user_id == user_id))
        ).scalar_one_or_none()
        if harness is None:
            raise TraceNotFoundError()
        if not harness.composition_id:
            return AgentQualityResponse(agent_id=agent_id, versions=[])
        try:
            raw = await self._client.trace_versions(identity, harness.composition_id)
        except AiUpstreamError as exc:
            raise _upstream(exc) from exc
        releases = dict(
            (
                await db.execute(
                    select(HarnessRelease.composition_digest, HarnessRelease.version).where(
                        HarnessRelease.harness_id == agent_id
                    )
                )
            ).all()
        )
        versions = [
            VersionQuality.model_validate({**v, "version": releases.get(v.get("composition_digest"))})
            for v in raw.get("versions", [])
            if isinstance(v, dict)
        ]
        return AgentQualityResponse(agent_id=agent_id, versions=versions)

    async def _owned_agent(self, db: AsyncSession, user_id: str, agent_id: str) -> Harness:
        harness = (
            await db.execute(select(Harness).where(Harness.id == agent_id, Harness.user_id == user_id))
        ).scalar_one_or_none()
        if harness is None:
            raise TraceNotFoundError()
        return harness

    async def issues(
        self, *, identity: TenantPrincipal, user_id: str, agent_id: str | None, since_ms: int | None, db: AsyncSession
    ) -> IssuesResponse:
        """The user's failures grouped into issues, named by agent, version and session."""
        params: dict[str, Any] = {}
        if agent_id:
            harness = await self._owned_agent(db, user_id, agent_id)
            if not harness.composition_id:
                return IssuesResponse(issues=[])
            params["composition_id"] = harness.composition_id
        if since_ms is not None:
            params["since_ms"] = str(since_ms)
        try:
            raw = await self._client.trace_issues(identity, params)
        except AiUpstreamError as exc:
            raise _upstream(exc) from exc
        found = [i for i in raw.get("issues", []) if isinstance(i, dict)]
        ai_ids = {str(e.get("session_id")) for i in found for e in i.get("examples", [])}
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
        compositions = {str(i.get("composition_id")) for i in found if i.get("composition_id")}
        agents = (
            (
                await db.execute(
                    select(Harness).where(Harness.user_id == user_id, Harness.composition_id.in_(compositions))
                )
            ).scalars().all()
            if compositions
            else []
        )
        agent_of = {a.composition_id: a for a in agents}
        releases = {
            digest: version
            for digest, version in (
                await db.execute(
                    select(HarnessRelease.composition_digest, HarnessRelease.version).where(
                        HarnessRelease.harness_id.in_({a.id for a in agents})
                    )
                )
            )
            if digest
        } if agents else {}
        out: list[Issue] = []
        for i in found:
            examples = [
                {**e, "session_id": by_ai[str(e["session_id"])].id, "version": releases.get(e.get("composition_digest"))}
                for e in i.get("examples", [])
                if str(e.get("session_id")) in by_ai
            ]
            if not examples:
                continue  # every example is in a session the user has no link to
            agent = agent_of.get(i.get("composition_id"))
            out.append(
                Issue.model_validate(
                    {
                        **i,
                        "agent_id": agent.id if agent else None,
                        "agent_name": agent.name if agent else None,
                        "versions": [
                            {**v, "version": releases.get(v.get("composition_digest"))} for v in i.get("versions", [])
                        ],
                        "examples": examples,
                    }
                )
            )
        return IssuesResponse(issues=out)

    async def judges(
        self, *, identity: TenantPrincipal, user_id: str, agent_id: str, db: AsyncSession
    ) -> JudgesResponse:
        harness = await self._owned_agent(db, user_id, agent_id)
        if not harness.composition_id:
            return JudgesResponse(agent_id=agent_id, judges=[])
        try:
            raw = await self._client.list_judges(identity, harness.composition_id)
        except AiUpstreamError as exc:
            raise _upstream(exc) from exc
        return JudgesResponse(
            agent_id=agent_id, judges=[Judge.model_validate(j) for j in raw.get("judges", []) if isinstance(j, dict)]
        )

    async def save_judge(
        self, *, identity: TenantPrincipal, user_id: str, agent_id: str, body: JudgeRequest, db: AsyncSession
    ) -> Judge:
        harness = await self._owned_agent(db, user_id, agent_id)
        if not harness.composition_id:
            raise HTTPException(
                status_code=409,
                detail={"error_code": "AGENT_NOT_PUBLISHED", "message": "publish the agent before giving it judges"},
            )
        try:
            raw = await self._client.save_judge(identity, {**body.model_dump(), "composition_id": harness.composition_id})
        except AiUpstreamError as exc:
            raise _upstream(exc) from exc
        return Judge.model_validate(raw)

    async def delete_judge(
        self, *, identity: TenantPrincipal, user_id: str, agent_id: str, judge_id: str, db: AsyncSession
    ) -> None:
        harness = await self._owned_agent(db, user_id, agent_id)
        current = await self.judges(identity=identity, user_id=user_id, agent_id=harness.id, db=db)
        if judge_id not in {j.id for j in current.judges}:
            raise TraceNotFoundError()  # not one of this agent's judges
        try:
            await self._client.delete_judge(identity, judge_id)
        except AiUpstreamError as exc:
            if exc.status_code == 404:
                raise TraceNotFoundError() from exc
            raise _upstream(exc) from exc
