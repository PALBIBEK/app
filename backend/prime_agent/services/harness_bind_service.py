"""Harness bind service — pin product harness to offered composition (D3)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from prime_agent.config import Settings, get_settings
from prime_agent.schemas.ai_identity import TenantPrincipal
from prime_agent.schemas.harness import (
    CatalogCompositionSummary,
    HarnessBindRequest,
    HarnessResponse,
    RunnablePin,
)
from prime_agent.services.ai_client import AiClient, AiUpstreamError
from prime_agent.services.harness_service import HarnessService
from prime_agent.services.platform_status_service import PlatformStatusService
from prime_agent.services.tool_publish_service import (
    ToolPublishError,
    publish_harness_composition,
    publish_owned_tool_uuids,
)

DEFAULT_COMPOSITION_ID = "githi-v1"
"""What a harness runs when it names no composition: the shipped githi coding agent."""


class BindDomainError(Exception):
    """Typed bind/projection failure with wire-safe to_dict()."""

    error_code: str = "BIND_ERROR"
    http_status: int = 422

    def __init__(self, message: str) -> None:
        super().__init__(message)

    def to_dict(self) -> dict[str, Any]:
        return {"error_code": self.error_code, "message": str(self)}


class NotBoundError(BindDomainError):
    error_code = "NOT_BOUND"


class StaleBindError(BindDomainError):
    error_code = "STALE_BIND"


class CatalogUnavailableError(BindDomainError):
    error_code = "CATALOG_UNAVAILABLE"
    http_status = status.HTTP_503_SERVICE_UNAVAILABLE


class CompositionNotOfferedError(BindDomainError):
    error_code = "COMPOSITION_NOT_OFFERED"


class ToolPublishBindError(BindDomainError):
    """UUID → ToolSet publish failed during harness bind (D6 / LOCAL_DEMO #3)."""

    error_code = "TOOL_PUBLISH_ERROR"
    http_status = 422

    def __init__(self, message: str, *, error_code: str | None = None, http_status: int | None = None) -> None:
        super().__init__(message)
        if error_code:
            self.error_code = error_code
        if http_status is not None:
            self.http_status = http_status


def raise_bind_http(exc: BindDomainError) -> None:
    raise HTTPException(
        status_code=exc.http_status,
        detail=exc.to_dict(),
    ) from exc


@dataclass(frozen=True, slots=True)
class _ResolvedTarget:
    composition_id: str
    composition_digest: str
    harness_version: str
    runtime: str


def _semver_key(semver: str | None) -> tuple[int, ...]:
    parts = []
    for part in (semver or "0").split("+")[0].split("-")[0].split("."):
        parts.append(int(part) if part.isdigit() else 0)
    return tuple(parts)


class HarnessBindService:
    """Validate against live catalog and persist immutable pins."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: AiClient | None = None,
        platform: PlatformStatusService | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._client = client or AiClient.from_settings(self._settings)
        self._platform = platform or PlatformStatusService(
            self._settings, client=self._client
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> HarnessBindService:
        return cls(settings)

    async def bind_to_composition(
        self,
        *,
        harness_id: str,
        user_id: str,
        identity: TenantPrincipal,
        request: HarnessBindRequest,
        db: AsyncSession,
    ) -> HarnessResponse:
        harness = await HarnessService.get_owned(harness_id, user_id, db)
        tool_ids = [str(t) for t in (harness.tools or []) if t]

        # LOCAL_DEMO #3 / D6: attached UUIDs → a ToolSet and a composition derived
        # from the base one (its prompt, profiles and harness version, plus the tools).
        base = await self._resolve(identity, request)
        if tool_ids and not request.composition_digest:
            try:
                base_comp = await self._client.get_artifact(
                    identity, "composition", base.composition_id, base.composition_digest
                )
                base_body = base_comp.get("body") or {}
                pinned_tools = base_body.get("tool_set") or {}
                base_tools = (
                    await self._client.get_artifact(
                        identity, "tool_set", pinned_tools["id"], pinned_tools["digest"]
                    )
                ).get("body") if pinned_tools.get("id") and pinned_tools.get("digest") else None
                published = await publish_owned_tool_uuids(
                    db=db,
                    user_id=user_id,
                    identity=identity,
                    tool_ids=tool_ids,
                    settings=self._settings,
                    # The supervisor refuses a tool with nothing to run it: say so here.
                    require_handlers=True,
                    base_tool_set=base_tools,
                    name=f"harness-{harness_id[:8]}-tools",
                    client=self._client,
                )
                digest = published.get("digest")
                set_name = published.get("name")
                if not digest or not set_name:
                    raise ToolPublishBindError(
                        "tool set publish did not return name/digest"
                    )
                comp = await publish_harness_composition(
                    identity=identity,
                    base_composition=base_body,
                    tool_set_name=str(set_name),
                    tool_set_digest=str(digest),
                    settings=self._settings,
                    composition_name=f"harness-{harness_id[:8]}-comp",
                    client=self._client,
                )
                comp_name = str(comp.get("name") or "")
                comp_digest = str(comp.get("digest") or "")
                if not comp_name or not comp_digest:
                    raise ToolPublishBindError(
                        "composition publish did not return name/digest"
                    )
                # Confirm offered before pinning (same gate as shipped compositions).
                summary = await self._fetch_offered(identity, comp_name, comp_digest)
                target = _ResolvedTarget(
                    composition_id=summary.artifact_id or comp_name,
                    composition_digest=summary.digest,
                    harness_version=base.harness_version,
                    runtime=base.runtime,
                )
            except ToolPublishError as exc:
                raise ToolPublishBindError(
                    str(exc),
                    error_code=getattr(exc, "error_code", None),
                    http_status=getattr(exc, "http_status", None),
                ) from exc
            except AiUpstreamError as exc:
                if exc.status_code and 400 <= exc.status_code < 500:
                    raise ToolPublishBindError(str(exc)) from exc  # the catalog refused it
                raise CatalogUnavailableError(
                    "AI catalog publish unavailable"
                ) from exc
        else:
            target = base

        now = datetime.now(UTC)
        harness.bind_status = "bound"
        harness.harness_version = target.harness_version
        harness.runtime = target.runtime
        harness.composition_id = target.composition_id
        harness.composition_digest = target.composition_digest
        harness.bound_at = now
        harness.bound_by = user_id
        # Services commit successful writes; get_db rolls back uncommitted sessions.
        await db.commit()
        await db.refresh(harness)
        return HarnessResponse.from_harness(harness)

    async def ensure_runnable_pin(
        self,
        *,
        harness_id: str,
        user_id: str,
        identity: TenantPrincipal,
        db: AsyncSession,
        require_run_allowed: bool = True,
    ) -> RunnablePin:
        harness = await HarnessService.get_owned(harness_id, user_id, db)

        if harness.bind_status == "unbound" or not harness.composition_digest:
            raise NotBoundError("harness is not bound to a composition")
        if harness.bind_status == "stale":
            raise StaleBindError("harness bind is stale; rebind required")
        if harness.bind_status != "bound":
            raise NotBoundError("harness bind is not runnable")

        if not (
            harness.harness_version
            and harness.runtime
            and harness.composition_id
            and harness.composition_digest
        ):
            raise NotBoundError("harness pin fields are incomplete")

        try:
            summary = await self._fetch_offered(
                identity, harness.composition_id, harness.composition_digest
            )
        except CompositionNotOfferedError as exc:
            harness.bind_status = "stale"
            await db.commit()
            raise StaleBindError("stored composition is no longer offered") from exc

        if summary.digest != harness.composition_digest:
            harness.bind_status = "stale"
            await db.commit()
            raise StaleBindError("stored composition digest no longer matches catalog")

        if require_run_allowed:
            await self._platform.ensure_ready(identity)

        return RunnablePin(
            harness_id=harness.id,
            harness_version=harness.harness_version,
            runtime=harness.runtime,
            composition_id=harness.composition_id,
            composition_digest=harness.composition_digest,
        )

    async def _resolve(
        self, identity: TenantPrincipal, request: HarnessBindRequest
    ) -> _ResolvedTarget:
        """What the catalog says now: never a digest or runtime typed into code.

        No digest → the offered version of that composition (the newest, when
        several are offered). Harness version from the composition itself, runtime
        from that harness version unless the request overrides it.
        """
        composition_id = (request.composition_id or DEFAULT_COMPOSITION_ID).strip()
        digest = request.composition_digest
        try:
            if not digest:
                offered = [
                    c for c in await self._client.list_compositions(identity)
                    if c.artifact_id == composition_id
                ]
                if not offered:
                    raise CompositionNotOfferedError(
                        f"composition {composition_id!r} is not offered"
                    )
                digest = max(offered, key=lambda c: _semver_key(c.semver)).digest
            summary = await self._fetch_offered(identity, composition_id, digest)
            if summary.digest != digest:
                raise CompositionNotOfferedError("catalog digest does not match requested pin")
            harness_version = summary.harness_version
            if not harness_version:
                raise CompositionNotOfferedError(
                    f"composition {composition_id!r} names no harness version"
                )
            runtime = request.runtime
            if not runtime:
                version = await self._client.get_harness_version(identity, harness_version)
                runtime = str(version.get("runtime") or "")
                if not runtime:
                    raise CompositionNotOfferedError(
                        f"harness version {harness_version!r} names no runtime"
                    )
        except AiUpstreamError as exc:
            if exc.status_code == 404:
                raise CompositionNotOfferedError(str(exc)) from exc
            raise CatalogUnavailableError("AI catalog unavailable") from exc
        return _ResolvedTarget(
            composition_id=composition_id,
            composition_digest=digest,
            harness_version=harness_version,
            runtime=runtime,
        )

    async def _fetch_offered(
        self,
        identity: TenantPrincipal,
        composition_id: str,
        digest: str,
    ) -> CatalogCompositionSummary:
        try:
            summary = await self._client.get_composition(identity, composition_id, digest)
        except AiUpstreamError as exc:
            if exc.status_code == 404:
                raise CompositionNotOfferedError("composition digest not found in catalog") from exc
            raise CatalogUnavailableError("AI catalog is unavailable") from exc

        if summary.status != "offered":
            raise CompositionNotOfferedError(
                f"composition status is {summary.status!r}, expected offered"
            )
        return summary
