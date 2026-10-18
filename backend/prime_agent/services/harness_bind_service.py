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

# Shipped MVP defaults — live catalog still validates offered + digest.
DEFAULT_COMPOSITION_ID = "coding-v1"
SHIPPED_COMPOSITION_PINS: dict[str, dict[str, str]] = {
    "coding-v1": {
        "harness_version": "coding-v1",
        "runtime": "harness",
        "composition_digest": (
            "sha256:2f3c7842ccc00b4f802dc22da3a463e57b2583b6a6d95ebedd3558c41f8ecf7d"
        ),
    },
    "githi-tools-v1": {
        "harness_version": "coding-v1",
        "runtime": "githi",
        "composition_digest": (
            "sha256:2f87308216eaceee01d4e9e87b4be60dc8e1eb336fc53da9f0cfa8a9eabaace9"
        ),
    },
}


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

        # LOCAL_DEMO #3 / D6: attached UUIDs → publish ToolSet + derived composition.
        # Empty tools keeps shipped coding-v1 pin (bind still works without custom tools).
        if tool_ids and not request.composition_id and not request.composition_digest:
            try:
                published = await publish_owned_tool_uuids(
                    db=db,
                    user_id=user_id,
                    identity=identity,
                    tool_ids=tool_ids,
                    settings=self._settings,
                    # Echo-path demo: allow attach/list without vendor keys / HttpTool.
                    # Custom invoke without http_url/handler remains residual.
                    require_handlers=False,
                    include_coding_base=True,
                    name=f"harness-{harness_id[:8]}-tools",
                )
                digest = published.get("digest")
                set_name = published.get("name")
                if not digest or not set_name:
                    raise ToolPublishBindError(
                        "tool set publish did not return name/digest"
                    )
                comp = await publish_harness_composition(
                    identity=identity,
                    tool_set_name=str(set_name),
                    tool_set_digest=str(digest),
                    settings=self._settings,
                    composition_name=f"harness-{harness_id[:8]}-comp",
                    harness_version="coding-v1",
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
                    harness_version="coding-v1",
                    runtime="harness",
                )
            except ToolPublishError as exc:
                raise ToolPublishBindError(
                    str(exc),
                    error_code=getattr(exc, "error_code", None),
                    http_status=getattr(exc, "http_status", None),
                ) from exc
            except AiUpstreamError as exc:
                raise CatalogUnavailableError(
                    "AI catalog publish unavailable"
                ) from exc
        else:
            target = self._resolve_target(request)
            summary = await self._fetch_offered(
                identity, target.composition_id, target.composition_digest
            )
            if summary.digest != target.composition_digest:
                raise CompositionNotOfferedError(
                    "catalog digest does not match requested pin"
                )

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

    def _resolve_target(self, request: HarnessBindRequest) -> _ResolvedTarget:
        composition_id = (request.composition_id or DEFAULT_COMPOSITION_ID).strip()
        shipped = SHIPPED_COMPOSITION_PINS.get(composition_id)
        if shipped is None and not request.composition_digest:
            raise CompositionNotOfferedError(
                f"unknown composition_id {composition_id!r}; provide composition_digest"
            )

        digest = request.composition_digest or (shipped or {}).get("composition_digest")
        if not digest:
            raise CompositionNotOfferedError("composition_digest is required")

        harness_version = (shipped or {}).get("harness_version") or composition_id
        runtime = request.runtime or (shipped or {}).get("runtime") or "harness"
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
