"""Production supervisor bootstrap — DB, harness catalog, composition catalog, sandbox."""

from __future__ import annotations

import logging
import os
from decimal import Decimal
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.app import SupervisorState, build_supervisor
from supervisor.catalog.loading.bootstrap import (
    bootstrap_composition_catalog,
)
from supervisor.catalog.storage import CompositionCatalog
from supervisor.config import Settings
from supervisor.db.engine import make_engine, session_factory
from supervisor.db.migrate import migrate
from supervisor.harness import HarnessCatalog, load_manifest
from supervisor.harness.repository import hydrate_harness_catalog
from supervisor.obs.metrics import setup_metrics
from supervisor.obs.tracing import setup_tracing
from supervisor.policy.quotas import TenantQuotaEnforcer, TenantQuotaLimits
from supervisor.policy.rate_limit import MemoryRateLimiter
from supervisor.sandbox.factory import create_sandbox_provider
from supervisor.sandbox.security.guards import assert_multi_tenant_sandbox_provider

logger = logging.getLogger(__name__)


def bootstrap_harness_catalog(versions_dir: Path) -> HarnessCatalog:
    catalog = HarnessCatalog()
    if not versions_dir.is_dir():
        return catalog
    for path in sorted(versions_dir.glob("*.yaml")) + sorted(versions_dir.glob("*.yml")):
        manifest = load_manifest(path.read_text(encoding="utf-8"))
        catalog.register_sync(manifest)  # defaults to HarnessStatus.ACTIVE
    return catalog


async def bootstrap_supervisor(
    settings: Settings,
    *,
    session_maker: async_sessionmaker[AsyncSession] | None = None,
    composition_catalog: CompositionCatalog | None = None,
) -> SupervisorState:
    """Build fully wired supervisor state for production or integration tests."""
    settings.require_auth_configured()
    # A half-set or invalid MODEL_GATEWAY_* configuration fails boot, not a turn.
    from supervisor.models import model_router
    from supervisor.models.browser import BrowserAccess

    model_router()

    if session_maker is None:
        engine = make_engine(settings.database_url)
        await migrate(engine)
        session_maker = session_factory(engine)

    harness_catalog = bootstrap_harness_catalog(settings.harness_versions_dir)
    # Versions tenants registered through the API (shipped ones come from the directory).
    await hydrate_harness_catalog(harness_catalog, session_maker)
    if composition_catalog is None and settings.load_composition_catalog:
        composition_catalog = bootstrap_composition_catalog(settings.resolved_catalog_dir())

    if composition_catalog is not None and settings.persist_composition_catalog:
        from supervisor.catalog.repository import hydrate_composition_catalog

        await hydrate_composition_catalog(composition_catalog, session_maker)

    assert_multi_tenant_sandbox_provider(
        settings.sandbox_provider,
        multi_tenant_strict=settings.multi_tenant_strict,
    )
    from supervisor.sandbox.security.guards import (
        assert_host_sandbox_allowed,
        host_sandbox_allowed,
    )

    assert_host_sandbox_allowed(settings.sandbox_provider)  # refused at boot, not per session
    # Stage 1 / D9: honor WORKSPACE_STORE + OBJECT_STORAGE + GRANT_STORE env.
    from supervisor.seams.providers.authz.grant_store_factory import (
        create_authz_service,
        create_grant_store,
    )
    from supervisor.seams.providers.gateway.virtual_key_client import maybe_virtual_key_client
    from supervisor.seams.providers.workspace.workspace_store_factory import (
        create_object_storage_for_workspace,
        create_workspace_hydrator,
        create_workspace_store,
    )

    objects = create_object_storage_for_workspace()
    workspace_store = create_workspace_store(
        session_factory=session_maker,
        objects=objects,
    )
    workspace_hydrator = create_workspace_hydrator(
        store=workspace_store,
        objects=objects,
        session_factory=session_maker,
    )
    try:
        sandbox = create_sandbox_provider(
            settings.sandbox_provider, hydrator=workspace_hydrator
        )
    except ModuleNotFoundError as exc:
        if not host_sandbox_allowed():
            raise RuntimeError(
                f"SANDBOX_PROVIDER={settings.sandbox_provider!r} is unavailable ({exc}); "
                "production and staging never fall back to running agent commands on the host"
            ) from exc
        if settings.multi_tenant_strict:
            # Falling back to host processes would silently drop tenant isolation.
            raise RuntimeError(
                f"SANDBOX_PROVIDER={settings.sandbox_provider!r} is unavailable ({exc}); "
                "MULTI_TENANT_STRICT forbids falling back to local_process"
            ) from exc
        # Optional provider dep missing (docker/e2b) — keep process up for /readyz,
        # on local processes, and say so: this is a development fallback.
        logger.warning(
            "SANDBOX_PROVIDER=%r is unavailable (%s); falling back to local_process",
            settings.sandbox_provider,
            exc,
        )
        sandbox = create_sandbox_provider("local_process", hydrator=workspace_hydrator)
    grant_store = create_grant_store(session_factory=session_maker)
    authz = create_authz_service(store=grant_store)
    vk_client = maybe_virtual_key_client()
    quotas = TenantQuotaEnforcer(
        session_maker,
        limits=TenantQuotaLimits(
            max_sessions_per_tenant=settings.tenant_max_sessions,
            max_sandboxes_per_tenant=settings.tenant_max_sandboxes,
            max_sessions_global=settings.global_max_sessions,
            max_cost_per_tenant=(
                Decimal(str(settings.tenant_max_cost))
                if settings.tenant_max_cost is not None
                else None
            ),
        ),
    )
    metrics = setup_metrics()
    setup_tracing()
    # Per-process sliding window; unset/0 disables tenant rate limiting.
    rate_limiter = (
        MemoryRateLimiter(max_per_window=settings.tenant_rate_limit_per_minute, window_ms=60_000)
        if settings.tenant_rate_limit_per_minute
        else None
    )
    return build_supervisor(
        session_factory=session_maker,
        catalog=harness_catalog,
        blob_dir=settings.blob_dir,
        sandbox_provider=sandbox,
        composition_catalog=composition_catalog,
        auth_token=settings.auth_token,
        auth_required=settings.auth_required,
        service_jwt_secret=settings.service_jwt_secret,
        service_jwt_audience=settings.service_jwt_audience,
        service_jwt_issuer=settings.service_jwt_issuer,
        needs_sandbox_check=settings.sandbox_provider not in ("none", "null", ""),
        drain_grace_s=settings.drain_grace_s,
        idle_reap_max_seconds=settings.idle_reap_max_seconds,
        idle_reap_interval_seconds=settings.idle_reap_interval_seconds,
        quotas=quotas,
        metrics=metrics,
        rate_limiter=rate_limiter,
        authz=authz,
        grant_store=grant_store,
        virtual_key_client=vk_client,
        workspace_store=workspace_store,
        browser=BrowserAccess.from_env(os.environ),
    )
