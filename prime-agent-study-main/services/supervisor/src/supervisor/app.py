"""Application wiring shared by main and tests."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.catalog.storage import CompositionCatalog
from supervisor.harness import HarnessCatalog
from supervisor.journal.commands import CommandStore
from supervisor.journal.reader import JournalReader
from supervisor.journal.writer import JournalWriter
from supervisor.memory.platform import MemoryPlatform
from supervisor.models.browser import BrowserAccess
from supervisor.obs.metrics import MetricsRegistry
from supervisor.policy.protocols import RateLimiterProtocol
from supervisor.policy.quotas import TenantQuotaEnforcer
from supervisor.sandbox.base import SandboxProvider
from supervisor.seams.services import AuthzService, WorkspaceStore
from supervisor.session import SessionManager, SessionRegistry
from supervisor.vault import Keyring, Vault

if TYPE_CHECKING:
    from supervisor.temporal.sessions import SessionWorkflows


@dataclass
class SupervisorState:
    manager: SessionManager
    reader: JournalReader
    commands: CommandStore
    composition_catalog: CompositionCatalog | None = None
    draining: bool = False
    auth_token: str | None = None
    auth_required: bool = False
    service_jwt_secret: str | None = None
    service_jwt_audience: str = "yal-supervisor"
    service_jwt_issuer: str = "prime-agent-api"
    check_db: Callable[[], Awaitable[bool]] | None = None
    check_sandbox: Callable[[], Awaitable[bool]] | None = None
    sandbox_provider: SandboxProvider | None = None
    drain_grace_s: float = 30.0
    idle_reap_max_seconds: float = 0.0
    idle_reap_interval_seconds: float = 60.0
    metrics: MetricsRegistry | None = None
    rate_limiter: RateLimiterProtocol | None = None
    authz: AuthzService | None = None
    grant_store: object | None = None
    workspace_store: WorkspaceStore | None = None
    vault: Vault | None = None
    writer: JournalWriter | None = None
    memory: MemoryPlatform | None = None
    # Every session runs as a Temporal workflow; None only in tests that drive the manager directly.
    workflows: SessionWorkflows | None = None


def build_supervisor(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    catalog: HarnessCatalog,
    blob_dir: Path | str | None = None,
    sandbox_provider: SandboxProvider | None = None,
    composition_catalog: CompositionCatalog | None = None,
    live_queue_max: int = 256,
    auth_token: str | None = None,
    auth_required: bool = False,
    service_jwt_secret: str | None = None,
    service_jwt_audience: str = "yal-supervisor",
    service_jwt_issuer: str = "prime-agent-api",
    needs_sandbox_check: bool = False,
    drain_grace_s: float = 30.0,
    idle_reap_max_seconds: float = 0.0,
    idle_reap_interval_seconds: float = 60.0,
    quotas: TenantQuotaEnforcer | None = None,
    metrics: MetricsRegistry | None = None,
    rate_limiter: RateLimiterProtocol | None = None,
    authz: AuthzService | None = None,
    grant_store: object | None = None,
    virtual_key_client: object | None = None,
    workspace_store: WorkspaceStore | None = None,
    browser: BrowserAccess | None = None,
    vault: Vault | None = None,
) -> SupervisorState:
    writer = JournalWriter(session_factory, blob_dir=blob_dir)
    reader = JournalReader(session_factory, blob_dir=blob_dir)
    commands = CommandStore(session_factory)
    registry = SessionRegistry()
    from supervisor.seams.services import GrantStore, SessionWorkspaceService, VirtualKeyClient

    gs = grant_store if isinstance(grant_store, GrantStore) else None
    vk = virtual_key_client if isinstance(virtual_key_client, VirtualKeyClient) else None
    workspace_service = SessionWorkspaceService(
        authz=authz,
        hydrator=getattr(sandbox_provider, "_hydrator", None),
    )
    memory = MemoryPlatform(session_factory)
    vault = vault or Vault(session_factory, Keyring.from_env())
    manager = SessionManager(
        session_factory=session_factory,
        writer=writer,
        reader=reader,
        registry=registry,
        catalog=catalog,
        sandbox_provider=sandbox_provider,
        live_queue_max=live_queue_max,
        composition_catalog=composition_catalog,
        quotas=quotas,
        authz=authz,
        grant_store=gs,
        workspace_service=workspace_service,
        virtual_key_client=vk,
        memory=memory,
        browser=browser,
        workspace_store=workspace_store,
        vault=vault,
    )

    async def check_db() -> bool:
        async with session_factory() as db:
            await db.execute(text("SELECT 1"))
        return True

    check_sandbox: Callable[[], Awaitable[bool]] | None = None
    if needs_sandbox_check and sandbox_provider is not None:

        async def _sandbox_ok() -> bool:
            return True

        check_sandbox = _sandbox_ok

    return SupervisorState(
        manager=manager,
        reader=reader,
        commands=commands,
        composition_catalog=composition_catalog,
        auth_token=auth_token,
        auth_required=auth_required,
        service_jwt_secret=service_jwt_secret,
        service_jwt_audience=service_jwt_audience,
        service_jwt_issuer=service_jwt_issuer,
        check_db=check_db,
        check_sandbox=check_sandbox,
        sandbox_provider=sandbox_provider,
        drain_grace_s=drain_grace_s,
        idle_reap_max_seconds=idle_reap_max_seconds,
        idle_reap_interval_seconds=idle_reap_interval_seconds,
        metrics=metrics,
        rate_limiter=rate_limiter,
        authz=authz,
        grant_store=gs,
        workspace_store=workspace_store,
        vault=vault,
        writer=writer,
        memory=memory,
    )
