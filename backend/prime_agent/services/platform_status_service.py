"""Product AI readiness aggregation (D2) — masterclass §11 service layer."""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime

from fastapi import HTTPException, status

from prime_agent.config import Settings, get_settings
from prime_agent.schemas.ai_identity import TenantPrincipal
from prime_agent.schemas.platform_status import (
    ComponentStatus,
    PlatformStatusResponse,
    WorkerAlert,
    WorkersStatus,
)
from prime_agent.services.ai_client import (
    AiClient,
    AiPlatformNotReadyError,
    ReadyzResult,
)


logger = logging.getLogger(__name__)


class PlatformStatusService:
    """Aggregate supervisor readiness into product run_allowed."""

    _cache: dict[str, tuple[float, PlatformStatusResponse]] = {}

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: AiClient | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._client = client or AiClient.from_settings(self._settings)

    @classmethod
    def from_settings(cls, settings: Settings) -> PlatformStatusService:
        return cls(settings)

    @classmethod
    def clear_cache(cls) -> None:
        cls._cache.clear()

    async def get_status(self, identity: TenantPrincipal) -> PlatformStatusResponse:
        ttl = getattr(self._settings, "ai_platform_status_cache_ttl_seconds", 10)
        cache_key = f"{identity.tenant_id}:{identity.principal_id}"
        now = time.monotonic()
        cached = self._cache.get(cache_key)
        if cached is not None and (now - cached[0]) < ttl:
            return cached[1]

        result = await self._client.readyz(identity)
        status_response = self._from_readyz(result)
        if result.ready:
            status_response.workers = await self._workers(identity)
        self._cache[cache_key] = (now, status_response)
        return status_response

    async def ensure_ready(self, identity: TenantPrincipal) -> PlatformStatusResponse:
        """Fail closed for mutating AI proxies (D4+)."""
        status_response = await self.get_status(identity)
        if not status_response.run_allowed:
            reason = None
            supervisor = status_response.components.get("supervisor")
            if supervisor is not None:
                reason = supervisor.reason
            raise AiPlatformNotReadyError(reason=reason)
        return status_response

    async def _workers(self, identity: TenantPrincipal) -> WorkersStatus | None:
        """The worker fleet in counts (no hosts or ids); None when it cannot be read.

        No worker does not block runs: work waits in Temporal and starts once one is up, so
        the product says so instead of disabling Test and Run.
        """
        try:
            raw = await self._client.worker_fleet(identity)
            known = {"no_workers", "backlog", "at_capacity"}
            return WorkersStatus(
                status=raw["status"],
                polling=int(raw["polling"]),
                slots_busy=int(raw["slots_busy"]),
                slots_max=int(raw["slots_max"]),
                backlog=int(raw["backlog"]),
                backlog_age_s=float(raw["backlog_age_s"]),
                alerts=[
                    WorkerAlert(code=a["code"], message=str(a["message"])[:300])
                    for a in raw.get("alerts", [])
                    if a.get("code") in known
                ],
            )
        except Exception:  # an older supervisor, Temporal down, a malformed answer
            logger.info("worker fleet status unavailable", exc_info=True)
            return None

    def _from_readyz(self, result: ReadyzResult) -> PlatformStatusResponse:
        component = ComponentStatus(
            ready=result.ready,
            status=result.status if result.ready else "not_ready",
            reason=None if result.ready else result.reason,
        )
        return PlatformStatusResponse(
            ready=result.ready,
            run_allowed=result.ready,
            checked_at=datetime.now(UTC),
            components={"supervisor": component},
        )


def raise_ai_not_ready_http(exc: AiPlatformNotReadyError) -> None:
    """Map typed gate error to HTTP 503."""
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=exc.to_dict(),
    ) from exc
