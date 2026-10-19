"""Session lifecycle — decisions live here; facts live in the journal."""

from __future__ import annotations

import asyncio
import os
import time
from decimal import Decimal
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.agent.events import (
    ApprovalRequired,
    ApprovalResolved,
    Envelope,
    ErrorEvent,
    MessageEnd,
    RunEnd,
    RunStart,
    ToolEnd,
    ToolStart,
    TurnStart,
)
from supervisor.agent.factory import create_runtime
from supervisor.agent.seams import RuntimeSeams
from supervisor.catalog.providers.binder import bind_runtime_seams
from supervisor.catalog import HarnessVersionMismatch
from supervisor.catalog.services.resolver import require_tenant_access, resolve_composition
from supervisor.catalog.storage import CompositionCatalog
from supervisor.catalog import MemoryProfile, SessionPins
from supervisor.catalog.repository import sync_composition
from supervisor.catalog.core.types import FileEffects
from supervisor.db.models import EventRow, ScopeRow, SessionIndexRow, SessionRow
from supervisor.harness import (
    ApprovalSpec,
    GuardSpec,
    HarnessCatalog,
    HarnessManifest,
    manifest_from_dict,
    manifest_to_dict,
    materialize_harness_binds,
)
from supervisor.harness.services.compat_checker import capabilities_for, check
from supervisor.harness.core.errors import UnknownHarness
from supervisor.harness.repository import harness_owner, sync_harness_version
from supervisor.harness.repository import visible_to as harness_visible_to
from supervisor.journal.reader import JournalReader
from supervisor.journal.writer import JournalWriter
from supervisor.policy.approvals import ApprovalBroker
from supervisor.policy.budget import BudgetExceeded, BudgetTracker
from supervisor.policy.guards import GuardAction, GuardConfig, LoopGuard, call_hash
from supervisor.policy.quotas import QuotaExceeded, TenantQuotaEnforcer
from supervisor.policy.sandbox_policy import ISOLATING_PROVIDERS, SandboxPolicy
from supervisor.sandbox.base import Sandbox, SandboxProvider, SnapshotUnsupported
from supervisor.catalog.providers.memory_resolve import load_pinned_memory_profile
from supervisor.sandbox.resolve import load_pinned_sandbox_profile, resolve_sandbox_create
from supervisor.tools import SandboxWorkspace
from supervisor.seams import (
    build_runtime_seams,
    CompactionPreparation,
    JobHandle,
    ModelGatewayClient,
    SubagentRequest,
    ToolClassifier,
    ToolInvokeContext,
    ToolProvider,
    ToolResult,
)
from supervisor.seams.services import (
    resolve_memory_scope,
    AuthzService,
    GrantStore,
    Principal,
    SessionWorkspaceService,
    SessionWorkspaceState,
    WorkspaceConflictError,
    VirtualKeyClient,
)
from supervisor.memory.binding import MemoryRequest, agent_pin, long_term_agent, resolve_binding
from supervisor.memory.platform import MemoryPlatform
from supervisor.models import model_router
from supervisor.models.browser import BrowserAccess, BrowserGrant
from supervisor.session.access import session_visibility, visible_to
from supervisor.session.live import LiveHub, LiveSubscription
from supervisor.session.memory_ops import CompactMemoryOutcome, MemoryOps, RecallMemoryOutcome
from supervisor.session.subagent_ops import SubagentOps
from supervisor.session.tool_outbox import ToolOutbox
from supervisor.session.tree import ChildRow, build_session_tree
from supervisor.session.modes import SessionMode, mode_for_manifest
from supervisor.session.recovery import Recovery, ResumeState
from supervisor.session.registry import SessionRegistry
from supervisor.session.workspaces import session_workspace

_log = logging.getLogger(__name__)
_LIVE_QUEUE_MAX = 256
_MUTATING_TOOLS = frozenset({"write", "edit", "edit_diff"})


class SessionError(Exception):
    pass


class SessionNotFound(SessionError):
    pass


class SessionTerminated(SessionError):
    pass


class SessionAwaitingApproval(SessionError):
    pass


class ToolInvokeNotConfigured(SessionError):
    pass


class SubagentNotConfigured(SessionError):
    pass


class MemoryNotConfigured(SessionError):
    pass


class WorkspaceUnavailable(SessionError):
    """The session has no scope-bound workspace or no sandbox to put it in."""


class RecoveryBlocked(SessionError):
    def __init__(
        self,
        state: ResumeState,
        tool_call_ids: tuple[str, ...],
        *,
        session_id: str,
        compaction_ids: tuple[str, ...] = (),
    ) -> None:
        self.state = state
        self.tool_call_ids = tool_call_ids
        self.compaction_ids = compaction_ids
        self.session_id = session_id
        tools = ", ".join(tool_call_ids) or "(none)"
        compactions = ", ".join(compaction_ids) or "(none)"
        super().__init__(
            f"recovery blocked state={state} session={session_id} "
            f"tools={tools} compactions={compactions}"
        )


@dataclass(frozen=True)
class ResumeResult:
    state: ResumeState
    session_id: str
    unpaired_tool_call_ids: tuple[str, ...] = ()
    unpaired_compaction_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ToolInvokeOutcome:
    tool_call_id: str
    result: ToolResult


@dataclass(frozen=True)
class SessionInfo:
    session_id: str
    tenant_id: str
    status: str
    harness_version: str
    runtime: str
    last_seq: int
    title: str | None = None
    message_count: int = 0
    is_busy: bool | None = None
    total_cost: float = 0.0



def session_model(manifest: HarnessManifest, pins: SessionPins | None) -> str | None:
    """The model a session names on every call: the composition's pinned route wins
    over the harness manifest's default model."""
    route = pins.model_route if pins is not None else None
    return route or manifest.model

class SessionManager:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        writer: JournalWriter,
        reader: JournalReader,
        registry: SessionRegistry,
        catalog: HarnessCatalog,
        sandbox_provider: SandboxProvider | None = None,
        live_queue_max: int = _LIVE_QUEUE_MAX,
        budget: BudgetTracker | None = None,
        guards: LoopGuard | None = None,
        approvals: ApprovalBroker | None = None,
        composition_catalog: CompositionCatalog | None = None,
        sandbox_policy: SandboxPolicy | None = None,
        quotas: TenantQuotaEnforcer | None = None,
        authz: AuthzService | None = None,
        grant_store: GrantStore | None = None,
        workspace_service: SessionWorkspaceService | None = None,
        virtual_key_client: VirtualKeyClient | None = None,
        memory: MemoryPlatform | None = None,
        browser: BrowserAccess | None = None,
    ) -> None:
        self._db = session_factory
        self._writer = writer
        self._reader = reader
        self._registry = registry
        self._catalog = catalog
        self._provider = sandbox_provider
        self._composition_catalog = composition_catalog
        self._live = LiveHub(maxsize=live_queue_max)
        self._fanout: dict[str, asyncio.Task[None]] = {}
        self._sandboxes: dict[str, Sandbox | None] = {}
        self._modes: dict[str, SessionMode] = {}
        self._seams: dict[str, RuntimeSeams] = {}
        # Long-term memory identity per live session: (owner, agent name).
        self._long_term: dict[str, tuple[str, str]] = {}
        self._workspace: dict[str, SessionWorkspaceState] = {}
        self._fallback_seams = build_runtime_seams()
        self._budget = budget or BudgetTracker(session_factory)
        self._guards = guards or LoopGuard()
        self._approvals = approvals or ApprovalBroker(session_factory)
        self._sandbox_policy = sandbox_policy or SandboxPolicy()
        self._quotas = quotas
        self._authz = authz
        self._grant_store = grant_store
        self._workspace_svc = workspace_service or SessionWorkspaceService(
            authz=authz,
            hydrator=getattr(sandbox_provider, "_hydrator", None),
        )
        self._vk_client = virtual_key_client
        self._browser = browser
        self._run_traces: dict[str, str] = {}
        self._tool_outbox = ToolOutbox(session_factory, writer)
        self._memory = memory or MemoryPlatform(session_factory)
        self._recovery = Recovery(
            registry=registry,
            reader=reader,
            catalog=catalog,
            sandbox_provider=sandbox_provider,
        )
        self._approval_manifest_cache: dict[str, HarnessManifest] = {}

    def _trace_for(self, session_id: str, runtime: Any | None = None) -> str | None:
        tid = self._run_traces.get(session_id)
        if tid:
            return tid
        rt = runtime if runtime is not None else self._registry.get(session_id)
        stream = getattr(rt, "_stream", None) if rt is not None else None
        if stream is not None and getattr(stream, "trace_id", None):
            return str(stream.trace_id)
        return None

    def subscribe_live(self, session_id: str) -> LiveSubscription:
        return self._live.subscribe(session_id)

    def unsubscribe_live(self, session_id: str, sub: LiveSubscription) -> None:
        self._live.unsubscribe(session_id, sub)

    def live_queue(self, session_id: str) -> asyncio.Queue:
        """Backward-compatible single subscription (tests)."""
        return self.subscribe_live(session_id).queue

    async def create(
        self,
        *,
        tenant_id: str,
        harness_version: str,
        runtime_name: str | None = None,
        composition_id: str | None = None,
        composition_digest: str | None = None,
        scope_id: str | None = None,
        principal_id: str | None = None,
        memory: MemoryRequest | dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> str:
        await sync_harness_version(self._catalog, self._db, harness_version)
        if not harness_visible_to(await harness_owner(self._db, harness_version), tenant_id):
            raise UnknownHarness(harness_version)  # another tenant's registered version
        manifest = await self._catalog.resolve(harness_version, for_create=True)
        check(
            manifest,
            capabilities_for(manifest.runtime),
            sandbox_provider=self._provider,
        )
        runtime_name = runtime_name or manifest.runtime
        session_id = session_id or uuid4().hex
        principal = Principal(
            id=principal_id or tenant_id,
            tenant_id=tenant_id,
        )
        ws_state = SessionWorkspaceState(
            scope_id=scope_id,
            principal=principal if scope_id else None,
        )

        pins_json: dict[str, str] | None = None
        pins: SessionPins | None = None
        if composition_id is not None or composition_digest is not None:
            if self._composition_catalog is None:
                raise RuntimeError(
                    "composition_id/composition_digest provided but no CompositionCatalog configured"
                )
            if composition_id is not None:
                await sync_composition(
                    self._composition_catalog, self._db, composition_id, composition_digest
                )
            pins = resolve_composition(
                self._composition_catalog,
                composition_id=composition_id,
                composition_digest=composition_digest,
            )
            require_tenant_access(self._composition_catalog, pins, tenant_id)
            if pins.harness_version != harness_version:
                raise HarnessVersionMismatch(
                    requested=harness_version,
                    composition=pins.harness_version,
                )
            pins_json = pins.to_dict()

        profile = None
        memory_profile = None
        if pins is not None and self._composition_catalog is not None:
            profile = load_pinned_sandbox_profile(pins, self._composition_catalog)
            memory_profile = load_pinned_memory_profile(pins, self._composition_catalog)

        if self._quotas is not None:
            sandbox_ids = [
                sid for sid, sbx in self._sandboxes.items() if sbx is not None
            ]
            live_sandboxes = await self._quotas.count_live_sandboxes(
                tenant_id, sandbox_ids
            )
            await self._quotas.check_create(
                tenant_id,
                requires_sandbox=manifest.requires_sandbox,
                live_sandbox_count=live_sandboxes,
            )

        if scope_id is not None:
            await self._ensure_scope_binding(scope_id, principal)
            await self._workspace_svc.require_sandbox_exec(ws_state)

        sandbox = await self._maybe_create_sandbox(
            manifest,
            session_id=session_id,
            harness_version=harness_version,
            sandbox_profile=profile,
        )
        if scope_id is not None and sandbox is not None:
            await self._workspace_svc.ensure_hydrated(ws_state, sandbox)

        runtime = await self._start_runtime(
            session_id,
            runtime_name=runtime_name,
            manifest=manifest,
            pins=pins,
            sandbox=sandbox,
            sandbox_profile=profile,
            memory_profile=memory_profile,
            tenant_id=tenant_id,
            principal=principal,
            ws_state=ws_state,
        )

        snap = manifest_to_dict(manifest)
        binding = await resolve_binding(
            self._memory.store,
            tenant_id=tenant_id,
            owner=principal.id,
            pin=agent_pin(
                harness_version=harness_version, manifest_snapshot=snap, composition_pins=pins_json
            ),
            request=memory if isinstance(memory, MemoryRequest) else MemoryRequest.from_dict(memory),
        )
        await self._writer.ensure_session(
            session_id,
            runtime=runtime_name,
            tenant_id=tenant_id,
            harness_version=harness_version,
        )
        async with self._db() as db:
            row = await db.get(SessionRow, session_id)
            assert row is not None
            row.runtime = runtime_name
            row.harness_version = harness_version
            row.manifest = snap
            row.composition_pins = pins_json
            row.tenant_id = tenant_id
            row.scope_id = scope_id
            row.status = "active"
            # Memory: owned by the creating principal, for this exact agent version.
            row.owner_principal_id = binding.owner
            row.agent_pin = binding.agent_pin
            row.memory = binding.to_dict()
            await db.commit()

        self._registry.put(session_id, runtime)
        self._sandboxes[session_id] = sandbox
        self._modes[session_id] = mode_for_manifest(manifest)
        self._workspace[session_id] = ws_state
        self._configure_policy(session_id, manifest, sandbox_profile=profile)
        self._start_fanout(session_id, runtime)
        return session_id

    @property
    def memory(self) -> MemoryPlatform:
        return self._memory

    async def _ensure_scope_binding(
        self, scope_id: str, principal: Principal
    ) -> None:
        """Create+own new scopes only. Existing scopes never re-bootstrap (WP-M7 IDOR)."""
        created = False
        async with self._db() as db:
            scope = await db.get(ScopeRow, scope_id)
            if scope is None:
                db.add(
                    ScopeRow(
                        id=scope_id,
                        type="personal",
                        owner_principal_id=principal.id,
                        name=scope_id,
                    )
                )
                await db.commit()
                created = True
        if created:
            if self._grant_store is None:
                raise RuntimeError(
                    "creating a scope requires grant_store for owner bootstrap"
                )
            await self._grant_store.bootstrap_owner(principal.id, scope_id)

    async def _start_runtime(
        self,
        session_id: str,
        *,
        runtime_name: str,
        manifest: HarnessManifest,
        pins: SessionPins | None,
        sandbox: Sandbox | None,
        sandbox_profile: Any | None,
        memory_profile: Any | None,
        tenant_id: str,
        principal: Principal,
        ws_state: SessionWorkspaceState,
    ) -> Any:
        """Build and start a session's runtime from what it pinned (create and resume alike).

        Everything the runtime uses comes from here: its pinned model, its seams
        (bound once, every model-calling seam on the session's gateway key), its
        sandbox, and the hook that marks the workspace dirty when a tool changes it.
        """
        model = session_model(manifest, pins)
        owner, agent = self._long_term.setdefault(
            session_id,
            (
                principal.id,
                long_term_agent(
                    composition_id=pins.composition_id if pins is not None else None,
                    harness_version=manifest.harness_version,
                ),
            ),
        )
        token = self._mint_gateway_key(
            session_id,
            tenant_id=tenant_id,
            principal=principal,
            model=model,
            summarizer_model=_summarizer_model(memory_profile),
            ws_state=ws_state,
        )
        seams = self._bind_seams(pins, gateway_token=token, session_id=session_id, model=model)
        session_dir: str | None = None
        if pins is not None and seams is not None:
            ws = session_workspace(session_id)
            materialize_harness_binds(
                ws,
                pins,
                seams,
                sandbox_profile=sandbox_profile,
                memory_profile=memory_profile,
                session_id=session_id,
                tenant_id=tenant_id,
                memory_scope=resolve_memory_scope(
                    session_id=session_id, tenant_id=tenant_id, owner=owner, agent=agent
                ),
            )
            session_dir = str(ws)
        if seams is not None:
            self._seams[session_id] = seams
        runtime = create_runtime(runtime_name, seams=seams, model=model, session_id=session_id)
        set_mutate = getattr(runtime, "set_on_mutate", None)
        if callable(set_mutate):
            set_mutate(lambda: self._workspace_svc.mark_dirty(ws_state))
        set_identity = getattr(runtime, "set_identity", None)
        if callable(set_identity) and (identity := self._long_term.get(session_id)) is not None:
            set_identity(owner=identity[0], agent=identity[1])
        await runtime.start(sandbox, session_dir)
        return runtime

    def memory_profile(self, row: SessionRow) -> MemoryProfile | None:
        """The memory profile a session pinned, if any."""
        if not row.composition_pins or self._composition_catalog is None:
            return None
        pins = SessionPins.from_dict(dict(row.composition_pins))
        return load_pinned_memory_profile(pins, self._composition_catalog)

    def session_model(self, row: SessionRow) -> str | None:
        """The model a session's calls name: its pinned route, else its manifest's model."""
        pins = SessionPins.from_dict(dict(row.composition_pins)) if row.composition_pins else None
        return session_model(self._pinned_manifest(row), pins)

    def strict_posture(self, row: SessionRow) -> bool:
        """A session pinned to a read-only sandbox gets no outward capabilities (browser model)."""
        profile = self._sandbox_profile_for_row(row)
        return profile is not None and profile.file_effects == FileEffects.READ_ONLY

    async def tool_env(self, session_id: str) -> dict[str, str]:
        """What this turn's commands may hold: the browse runner's model access, if any.

        A fresh browser capability per turn (an hour long), bound to the session,
        its owner and its model; none for strict sessions or without a model.
        """
        if self._browser is None:
            return {}
        row = await self._get_session_row(session_id)
        model = self.session_model(row)
        if not model or self.strict_posture(row):
            return {}
        grant = BrowserGrant(
            session_id=session_id,
            tenant_id=row.tenant_id,
            actor_id=row.owner_principal_id or "",
            model=model,
        )
        return self._browser.env(grant)

    def session_model_client(self, session_id: str, row: SessionRow) -> ModelGatewayClient | None:
        """The model client the session's own calls go through (and so its key and budget)."""
        seams = self._seams_for_session(session_id, row)
        return seams.model_gateway if seams is not None else None

    @property
    def browser(self) -> BrowserAccess | None:
        return self._browser

    def tool_denial(self, session_id: str, name: str, provider: ToolProvider | None) -> str | None:
        """Why the pinned sandbox profile forbids calling ``name`` now, or None if it may run."""
        tool_class = provider.tool_class(name) if isinstance(provider, ToolClassifier) else None
        return self._sandbox_policy.check_tool_invoke(session_id, name, tool_class=tool_class)

    def tool_context(self, session_id: str, tenant_id: str) -> dict[str, Any]:
        """``ToolInvokeContext.extra`` for a session: identity, its workspace, change hook."""
        extra: dict[str, Any] = {"session_id": session_id, "tenant_id": tenant_id}
        if (identity := self._long_term.get(session_id)) is not None:
            extra["owner"], extra["agent"] = identity
        runtime_workspace = getattr(self._registry.get(session_id), "workspace", None)
        sandbox = self._sandboxes.get(session_id)
        if runtime_workspace is not None:
            extra["workspace"] = runtime_workspace
        elif sandbox is not None:
            extra["workspace"] = SandboxWorkspace(sandbox)
        if (ws := self._workspace.get(session_id)) is not None:
            extra["on_mutate"] = lambda: self._workspace_svc.mark_dirty(ws)
        return extra

    def _mint_gateway_key(
        self,
        session_id: str,
        *,
        tenant_id: str,
        principal: Principal,
        model: str | None,
        ws_state: SessionWorkspaceState,
        summarizer_model: str | None = None,
    ) -> str | None:
        """A gateway key for this session, limited to the models it calls; None without a key service.

        Those are its own model and, when its memory profile compacts with a
        dedicated summarizer, that model too: a key limited to the session's
        model alone has the gateway refuse every summary.

        Through the routed model gateway the key is limited to the gateway model
        group the session's model is served by (an alias's target, a discovered
        group's own id).
        """
        if self._vk_client is None:
            return None
        router = model_router()

        def group(name: str) -> str:
            return (router.target_for(name) if router is not None else None) or name

        allowed = list(dict.fromkeys(group(m) for m in (model, summarizer_model) if m)) if model else []
        minted = self._vk_client.mint(
            tenant_id=tenant_id,
            session_id=session_id,
            principal_id=principal.id,
            allowed_models=allowed,
            scope_id=ws_state.scope_id,
        )
        ws_state.virtual_key_token = minted.token
        ws_state.virtual_key_id = minted.key_id
        return minted.token

    def _bind_seams(
        self,
        pins: SessionPins | None,
        *,
        gateway_token: str | None,
        session_id: str,
        model: str | None,
    ) -> RuntimeSeams | None:
        """The session's seams: from its pins, else (unpinned) just a keyed gateway, if any.

        Every model-calling seam carries the session's key and, as its default,
        the session's pinned model.
        """
        if pins is not None and self._composition_catalog is not None:
            return bind_runtime_seams(
                pins,
                self._composition_catalog,
                gateway_token=gateway_token,
                session_id=session_id,
                model=model,
            )
        if gateway_token is not None and (os.environ.get("GATEWAY_BASE_URL") or model_router()):
            return build_runtime_seams(
                gateway_token=gateway_token, session_id=session_id, default_model=model
            )
        return None

    async def get(
        self, session_id: str, *, tenant_id: str, principal_id: str | None = None
    ) -> SessionInfo | None:
        """The session, if it is in ``tenant_id`` and visible to ``principal_id`` (``session.access``)."""
        async with self._db() as db:
            row = await db.get(SessionRow, session_id)
            if row is None or row.tenant_id != tenant_id:
                return None
            if not visible_to(row.owner_principal_id, tenant_id=tenant_id, principal_id=principal_id):
                return None
            idx = await db.get(SessionIndexRow, session_id)
            is_busy: bool | None = None
            runtime = self._registry.get(session_id)
            if runtime is not None:
                try:
                    state = await runtime.get_state()
                    is_busy = state.is_busy
                except Exception:
                    is_busy = None
            return SessionInfo(
                session_id=row.id,
                tenant_id=row.tenant_id,
                status=row.status,
                harness_version=row.harness_version,
                runtime=row.runtime,
                last_seq=int(row.last_seq),
                title=idx.title if idx else None,
                message_count=int(idx.message_count) if idx else 0,
                is_busy=is_busy,
                total_cost=float(idx.total_cost) if idx else 0.0,
            )

    async def list_sessions(
        self, *, tenant_id: str, principal_id: str | None = None
    ) -> list[SessionInfo]:
        async with self._db() as db:
            rows = (
                await db.execute(
                    select(SessionRow, SessionIndexRow)
                    .outerjoin(
                        SessionIndexRow,
                        SessionIndexRow.session_id == SessionRow.id,
                    )
                    .where(SessionRow.tenant_id == tenant_id)
                    .where(session_visibility(tenant_id, principal_id))
                    .order_by(SessionRow.created_at.desc())
                )
            ).all()
        out: list[SessionInfo] = []
        for row, idx in rows:
            out.append(
                SessionInfo(
                    session_id=row.id,
                    tenant_id=row.tenant_id,
                    status=row.status,
                    harness_version=row.harness_version,
                    runtime=row.runtime,
                    last_seq=int(row.last_seq),
                    title=idx.title if idx else None,
                    message_count=int(idx.message_count) if idx else 0,
                    total_cost=float(idx.total_cost) if idx else 0.0,
                )
            )
        return out

    async def ensure_ready(self, session_id: str) -> None:
        """Resume if cold; raise RecoveryBlocked / SessionTerminated before accepting work."""
        await self._require_active(session_id)
        if self._registry.get(session_id) is not None:
            return
        result = await self.resume(session_id)
        if result.state not in (ResumeState.WARM, ResumeState.COLD):
            raise RecoveryBlocked(
                result.state,
                result.unpaired_tool_call_ids,
                session_id=session_id,
                compaction_ids=result.unpaired_compaction_ids,
            )

    async def prepare_turn(self, session_id: str) -> None:
        """Everything that must hold before a turn may start; raises the reason it cannot."""
        await self.ensure_ready(session_id)
        ws = self._workspace.get(session_id)
        if ws is not None:
            self._workspace_svc.assert_ready(ws)
        state = await self._budget.check(session_id)
        if state.exceeded:
            raise BudgetExceeded.from_state(state)

    async def send(
        self,
        session_id: str,
        message: str,
        *,
        command_id: str,
        inbound_trace_id: str | None = None,
    ) -> None:
        await self.prepare_turn(session_id)
        self._budget.begin_turn(session_id)
        runtime = self._registry.get(session_id)
        mode = self._modes.get(session_id)
        assert runtime is not None
        if inbound_trace_id:
            seed = getattr(runtime, "seed_trace_id", None)
            if seed is not None:
                seed(inbound_trace_id)
        await runtime.send(message, command_id=command_id)
        if mode == SessionMode.STATELESS_PER_TURN:
            pass

    async def abort(self, session_id: str) -> None:
        runtime = self._registry.get(session_id)
        if runtime is None:
            return
        await runtime.abort()

    async def resume(self, session_id: str) -> ResumeResult:
        row = await self._get_session_row(session_id)
        decision = await self._recovery.decide(session_id, session_row=row)
        if decision.state == ResumeState.WARM:
            return ResumeResult(state=ResumeState.WARM, session_id=session_id)
        if decision.state == ResumeState.NEEDS_REVIEW:
            return ResumeResult(
                state=ResumeState.NEEDS_REVIEW,
                session_id=session_id,
                unpaired_tool_call_ids=decision.unpaired_tool_call_ids,
                unpaired_compaction_ids=decision.unpaired_compaction_ids,
            )
        if decision.state == ResumeState.FAILED:
            async with self._db() as db:
                srow = await db.get(SessionRow, session_id)
                if srow is not None:
                    srow.status = "failed"
                    await db.commit()
            return ResumeResult(
                state=ResumeState.FAILED,
                session_id=session_id,
                unpaired_tool_call_ids=decision.unpaired_tool_call_ids,
            )
        if decision.state == ResumeState.UNRECOVERABLE:
            return ResumeResult(
                state=ResumeState.UNRECOVERABLE,
                session_id=session_id,
                unpaired_tool_call_ids=decision.unpaired_tool_call_ids,
            )

        harness = self._pinned_manifest(row)
        check(
            harness,
            capabilities_for(harness.runtime),
            sandbox_provider=self._provider,
        )
        pins: SessionPins | None = None
        profile = None
        memory_profile = None
        if row.composition_pins and self._composition_catalog is not None:
            pins = SessionPins.from_dict(dict(row.composition_pins))
            await sync_composition(
                self._composition_catalog, self._db, pins.composition_id, pins.composition_digest
            )
            profile = load_pinned_sandbox_profile(pins, self._composition_catalog)
            memory_profile = load_pinned_memory_profile(pins, self._composition_catalog)
        principal = Principal(
            id=row.owner_principal_id or row.tenant_id, tenant_id=row.tenant_id
        )
        ws_state = self._workspace.get(session_id) or SessionWorkspaceState(
            scope_id=row.scope_id,
            principal=principal if row.scope_id else None,
        )
        existing = self._sandboxes.get(session_id)
        if existing is not None:
            sandbox = existing
        else:
            sandbox = await self._maybe_create_sandbox(
                harness,
                session_id=session_id,
                harness_version=row.harness_version,
                sandbox_profile=profile,
            )
            if row.scope_id is not None and sandbox is not None:
                await self._workspace_svc.ensure_hydrated(ws_state, sandbox)
        # V-6: re-bind from stored pins; do not re-resolve floating catalog.
        runtime = await self._start_runtime(
            session_id,
            runtime_name=row.runtime,
            manifest=harness,
            pins=pins,
            sandbox=sandbox,
            sandbox_profile=profile,
            memory_profile=memory_profile,
            tenant_id=row.tenant_id,
            principal=principal,
            ws_state=ws_state,
        )
        self._workspace[session_id] = ws_state
        history = await self._reader.latest_history(session_id)
        # D13: reconstruct open-span state from journal after cold resume / restart.
        try:
            from supervisor.obs.tracing import get_projector

            proj = get_projector()
            if proj is not None and hasattr(proj, "reconstruct"):
                journal = await self._reader.read(session_id, since=-1)
                harness_ver = row.harness_version
                harness_id = harness_ver
                if row.composition_pins and isinstance(row.composition_pins, dict):
                    cid = row.composition_pins.get("composition_id")
                    if cid:
                        harness_id = str(cid)
                proj.reconstruct(
                    journal,
                    session_id=session_id,
                    tenant_id=row.tenant_id,
                    harness_id=harness_id,
                    harness_version=harness_ver,
                )
        except Exception:
            _log.exception("span reconstruct on resume failed session=%s", session_id)
        _ = history
        self._registry.put(session_id, runtime)
        self._sandboxes[session_id] = sandbox
        self._modes[session_id] = mode_for_manifest(harness)
        self._configure_policy(session_id, harness, sandbox_profile=profile)
        self._start_fanout(session_id, runtime)
        async with self._db() as db:
            srow = await db.get(SessionRow, session_id)
            if srow is not None and srow.status == "paused":
                srow.status = "active"
                await db.commit()
        return ResumeResult(
            state=ResumeState.COLD,
            session_id=session_id,
            unpaired_tool_call_ids=decision.unpaired_tool_call_ids,
        )

    async def fork(
        self, session_id: str, *, at_seq: int | None = None, new_id: str | None = None
    ) -> str:
        """Copy the session (up to ``at_seq``) into ``new_id``; safe to retry with the same id.

        Each copied event is keyed by its source event, so a retry after a crash
        mid-copy carries on where it stopped instead of duplicating events.
        """
        row = await self._get_session_row(session_id)
        events = await self._reader.read(session_id, since=-1)
        if at_seq is not None:
            events = [e for e in events if e.seq <= at_seq]

        new_id = new_id or uuid4().hex
        await self._writer.ensure_session(
            new_id,
            runtime=row.runtime,
            tenant_id=row.tenant_id,
            harness_version=row.harness_version,
        )
        async with self._db() as db:
            nrow = await db.get(SessionRow, new_id)
            assert nrow is not None
            nrow.runtime = row.runtime
            nrow.harness_version = row.harness_version
            nrow.manifest = row.manifest
            nrow.composition_pins = row.composition_pins
            nrow.tenant_id = row.tenant_id
            # A fork is the same person's same agent, with the same notebooks.
            nrow.owner_principal_id = row.owner_principal_id
            nrow.agent_pin = row.agent_pin
            nrow.memory = row.memory
            nrow.scope_id = row.scope_id
            nrow.status = "active"
            await db.commit()

        for env in events:
            await self._writer.append(
                new_id,
                env.payload,
                turn_id=env.turn_id,
                trace_id=env.trace_id,
                event_key=f"fork:{env.event_id}",
            )
        return new_id

    async def terminate(self, session_id: str) -> None:
        # D13 T9: reap open spans via session_terminal before tearing down the session.
        try:
            import time

            from supervisor.obs.reaper import ReaperConfig, SpanReaper
            from supervisor.obs.tracing import get_projector

            proj = get_projector()
            if proj is not None:
                if hasattr(proj, "force_close_session"):
                    proj.force_close_session(
                        session_id,
                        end_ms=int(time.time() * 1000),
                        closed_by="session_terminal",
                    )
                else:
                    SpanReaper(
                        projector=proj,
                        config=ReaperConfig(),
                        session_terminal=True,
                    ).tick()
                if hasattr(proj, "drop_session"):
                    proj.drop_session(session_id)
        except Exception:
            _log.exception("span session_terminal reap failed session=%s", session_id)

        ws_state = self._workspace.get(session_id)
        sandbox = self._sandboxes.get(session_id)
        if ws_state is not None and sandbox is not None:
            try:
                await self._workspace_svc.sync_if_dirty(ws_state, sandbox)
            except WorkspaceConflictError:
                _log.exception("workspace conflict on terminate session=%s", session_id)
            except Exception:
                _log.exception("workspace sync failed on terminate session=%s", session_id)
        if self._vk_client is not None:
            try:
                self._vk_client.revoke_session(session_id)
            except Exception:
                _log.exception("virtual key revoke failed session=%s", session_id)
        runtime = self._registry.remove(session_id)
        task = self._fanout.pop(session_id, None)
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if runtime is not None:
            try:
                await runtime.stop()
            except Exception:
                _log.exception("stop failed session=%s", session_id)
        sandbox = self._sandboxes.pop(session_id, None)
        if sandbox is not None:
            try:
                await sandbox.destroy()
            except Exception:
                _log.exception("sandbox destroy failed session=%s", session_id)
        self._modes.pop(session_id, None)
        self._seams.pop(session_id, None)
        self._long_term.pop(session_id, None)
        self._workspace.pop(session_id, None)
        self._sandbox_policy.clear(session_id)
        self._guards.clear(session_id)
        self._live.drop_session(session_id)
        async with self._db() as db:
            row = await db.get(SessionRow, session_id)
            if row is not None:
                row.status = "terminated"
                await db.commit()

    def reattach_sandbox(self, session_id: str, sandbox: Sandbox) -> None:
        """Bind a live sandbox discovered after supervisor restart."""
        self._sandboxes[session_id] = sandbox

    async def rehydrate_workspace(self, session_id: str) -> None:
        """Force hydrate durable SoT into the live sandbox cache (D9 LOCAL_DEMO)."""
        await self._require_active(session_id)
        ws = self._workspace.get(session_id)
        sandbox = self._sandboxes.get(session_id)
        if ws is None or ws.scope_id is None:
            raise WorkspaceUnavailable("session has no scope-bound workspace")
        if sandbox is None:
            raise WorkspaceUnavailable("session has no sandbox to rehydrate")
        await self._workspace_svc.ensure_hydrated(ws, sandbox, force=True)

    async def _sync_workspace_fail_closed(
        self,
        session_id: str,
        ws_state: SessionWorkspaceState,
        sandbox: Sandbox,
    ) -> None:
        """Sync dirty paths; on CAS conflict rehydrate durable bytes then re-raise."""
        try:
            await self._workspace_svc.sync_if_dirty(ws_state, sandbox, force=True)
        except WorkspaceConflictError:
            await self._workspace_svc.recover_cas_conflict(ws_state, sandbox)
            raise

    async def session_status(self, session_id: str) -> str | None:
        async with self._db() as db:
            row = await db.get(SessionRow, session_id)
            return None if row is None else str(row.status)

    async def reap_idle_sessions(self, *, max_idle_seconds: float) -> list[str]:
        """Pause sticky or terminate stateless sessions idle longer than threshold."""
        if max_idle_seconds <= 0:
            return []
        threshold = datetime.now(timezone.utc) - timedelta(seconds=max_idle_seconds)
        reaped: list[str] = []
        for sid in list(self._registry.all()):
            runtime = self._registry.get(sid)
            if runtime is None:
                continue
            try:
                state = await runtime.get_state()
            except Exception:
                _log.exception("reap_idle state failed session=%s", sid)
                continue
            if not state.settled:
                continue
            if not await self._is_session_idle(sid, threshold):
                continue
            await self._reap_idle_session(sid)
            reaped.append(sid)
        return reaped

    async def _is_session_idle(self, session_id: str, threshold: datetime) -> bool:
        since = await self._session_last_activity(session_id)
        if since is None:
            return False
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)
        return since <= threshold

    async def _session_last_activity(self, session_id: str) -> datetime | None:
        async with self._db() as db:
            idx = await db.get(SessionIndexRow, session_id)
            if idx is not None and idx.last_event_at is not None:
                return idx.last_event_at
            row = await db.get(SessionRow, session_id)
            if row is None:
                return None
            return row.updated_at or row.created_at

    async def _reap_idle_session(self, session_id: str) -> None:
        mode = self._modes.get(session_id, SessionMode.STICKY)
        if mode == SessionMode.STATELESS_PER_TURN:
            await self.terminate(session_id)
            return

        sandbox = self._sandboxes.get(session_id)
        runtime = self._registry.get(session_id)
        if sandbox is not None:
            try:
                await sandbox.pause()
            except Exception:
                _log.exception("reap_idle pause failed session=%s", session_id)
                try:
                    await sandbox.destroy()
                except Exception:
                    _log.exception("reap_idle destroy failed session=%s", session_id)
                self._sandboxes.pop(session_id, None)
        if runtime is not None:
            try:
                await runtime.stop()
            except Exception:
                _log.exception("reap_idle stop failed session=%s", session_id)
        self._registry.remove(session_id)
        task = self._fanout.pop(session_id, None)
        if task is not None:
            task.cancel()
        async with self._db() as db:
            row = await db.get(SessionRow, session_id)
            if row is not None:
                row.status = "paused"
                await db.commit()

    async def drain(self, *, timeout_seconds: float = 30.0) -> None:
        """Wait for in-flight turns to settle; pause sticky, stop the rest."""
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            unsettled = False
            for sid in self._registry.all():
                runtime = self._registry.get(sid)
                if runtime is None:
                    continue
                try:
                    state = await runtime.get_state()
                except Exception:
                    continue
                if not state.settled:
                    unsettled = True
                    break
            if not unsettled:
                break
            await asyncio.sleep(0.05)

        for sid in list(self._modes.keys()):
            mode = self._modes.get(sid)
            sandbox = self._sandboxes.get(sid)
            runtime = self._registry.get(sid)
            if mode == SessionMode.STICKY and sandbox is not None:
                try:
                    await sandbox.pause()
                except SnapshotUnsupported:
                    # Expected for providers without snapshots (local_process): the
                    # adopting worker starts the sandbox afresh. Not an error.
                    _log.info("drain: %s keeps no snapshot session=%s", sandbox.info.provider, sid)
                except Exception:
                    _log.exception("drain pause failed session=%s", sid)
            if runtime is not None:
                try:
                    await runtime.stop()
                except Exception:
                    _log.exception("drain stop failed session=%s", sid)
            self._registry.remove(sid)
            task = self._fanout.pop(sid, None)
            if task is not None:
                task.cancel()

        paused_ids: list[str] = []
        for sid in list(self._modes.keys()):
            if self._modes.get(sid) == SessionMode.STICKY and sid in self._sandboxes:
                paused_ids.append(sid)

        if paused_ids:
            async with self._db() as db:
                for sid in paused_ids:
                    row = await db.get(SessionRow, sid)
                    if row is not None and row.status == "active":
                        row.status = "paused"
                await db.commit()

    async def _maybe_create_sandbox(
        self,
        manifest: HarnessManifest,
        *,
        session_id: str | None = None,
        harness_version: str | None = None,
        sandbox_profile: Any | None = None,
    ) -> Sandbox | None:
        provider, digest = resolve_sandbox_create(
            manifest,
            deploy_provider=self._provider,
            profile=sandbox_profile,
            hydrator=self._workspace_svc.hydrator,
        )
        if provider is None:
            return None
        labels: dict[str, str] = {}
        if session_id:
            labels["supervisor.session_id"] = session_id
        if harness_version or manifest.harness_version:
            labels["supervisor.harness_version"] = (
                harness_version or manifest.harness_version
            )
        if sandbox_profile is not None:
            labels["supervisor.sandbox_profile"] = sandbox_profile.name
        return await provider.create(image=digest, labels=labels or None)

    def _pinned_manifest(self, row: SessionRow) -> HarnessManifest:
        """Resume/recovery always use the snapshot stored on the session."""
        if row.manifest:
            return manifest_from_dict(dict(row.manifest))
        return self._catalog.resolve_sync(row.harness_version, for_create=False)

    def _start_fanout(self, session_id: str, runtime: Any) -> None:
        old = self._fanout.get(session_id)
        if old is not None:
            old.cancel()
        self._fanout[session_id] = asyncio.create_task(
            self._fan_out(session_id, runtime),
            name=f"fanout-{session_id[:8]}",
        )

    async def _fan_out(self, session_id: str, runtime: Any) -> None:
        mode = self._modes.get(session_id, SessionMode.STICKY)
        try:
            async for envelope in runtime.events():
                payload = envelope.payload

                if isinstance(payload, TurnStart):
                    self._budget.begin_turn(session_id)

                if isinstance(payload, ApprovalRequired):
                    payload, _parked = await self._handle_approval_required(
                        session_id, envelope.turn_id, payload
                    )

                await self._record(
                    session_id,
                    payload,
                    turn_id=envelope.turn_id,
                    trace_id=envelope.trace_id,
                    event_id=envelope.event_id,
                    ts_ms=envelope.ts_ms,
                )

                if isinstance(payload, RunStart):
                    self._guards.reset(session_id)  # stall counts tool calls within one run
                verdict = (
                    self._guards.observe(
                        session_id,
                        payload.tool_name,
                        call_hash(payload.tool_name, payload.args),
                    )
                    if isinstance(payload, ToolStart)
                    else None
                )
                if verdict is not None and verdict.tripped and verdict.action == GuardAction.ABORT:
                    await self._policy_abort(
                        session_id,
                        runtime,
                        error_class="LoopGuard",
                        detail=verdict.detail or "loop_detected",
                    )
                    return

                if isinstance(payload, MessageEnd) and payload.usage is not None:
                    usage = payload.usage
                    bstate = await self._budget.record(
                        session_id, Decimal(str(usage.cost_total)), usage.total_tokens
                    )
                    if bstate.exceeded:
                        await self._policy_abort(
                            session_id,
                            runtime,
                            error_class="BudgetExceeded",
                            detail=bstate.reason or "budget exceeded",
                            status="budget_exceeded",
                        )
                        return

                if isinstance(payload, RunEnd) and mode == SessionMode.STATELESS_PER_TURN:
                    await self._stop_runtime_keep_session(session_id)
                    return
        except Exception as exc:
            _log.exception("fan-out failed session=%s", session_id)
            try:
                await self._record(
                    session_id,
                    ErrorEvent(
                        error_class=type(exc).__name__,
                        retryable=False,
                        detail=str(exc),
                    ),
                    turn_id=None,
                    trace_id=self._trace_for(session_id, runtime),
                )
            except Exception:
                _log.exception("failed to write terminal event session=%s", session_id)
            self._registry.remove(session_id)

    async def respond_approval(
        self, approval_id: str, choice: str, *, responded_by: str | None = None
    ) -> ApprovalResolved:
        approval = await self._approvals.respond(approval_id, choice, responded_by=responded_by)
        resolved = ApprovalResolved(
            approval_id=approval.approval_id,
            choice=choice,
            status=approval.status,
            responded_by=responded_by,
        )
        await self._record(
            approval.session_id,
            resolved,
            turn_id=approval.turn_id,
            trace_id=self._trace_for(approval.session_id),
        )
        # Forward to harness subprocess if still warm.
        runtime = self._registry.get(approval.session_id)
        if runtime is not None and approval.harness_request_id:
            send_ui = getattr(runtime, "respond_ui", None)
            if send_ui is not None:
                await send_ui(
                    harness_request_id=approval.harness_request_id,
                    choice=choice,
                )
        remaining = await self._approvals.pending(approval.session_id)
        if not remaining:
            async with self._db() as db:
                row = await db.get(SessionRow, approval.session_id)
                if row is not None and row.status == "awaiting_approval":
                    row.status = "active"
                    await db.commit()
        return resolved

    async def pending_approvals(self, session_id: str):
        return await self._approvals.pending(session_id)

    async def expire_overdue_approvals(
        self, *, now: float | None = None
    ) -> list[Any]:
        """Sweep overdue pending approvals using per-session manifest on_timeout."""
        from supervisor.policy.types import is_allow_choice

        pending = await self._approvals.list_pending_all()
        session_policies: dict[str, Literal["deny", "allow"]] = {}
        for approval in pending:
            sid = approval.session_id
            if sid in session_policies:
                continue
            try:
                row = await self._get_session_row(sid)
                manifest = self._pinned_manifest(row)
                if manifest.approvals is not None:
                    session_policies[sid] = manifest.approvals.on_timeout
                else:
                    session_policies[sid] = "deny"
            except Exception:
                session_policies[sid] = "deny"

        expired = await self._approvals.expire_due(
            # ``now`` is wall-clock epoch seconds; the broker works in milliseconds.
            now_ms=int(now * 1000) if now is not None else None,
            default_choice="deny",
            session_defaults=session_policies,
        )
        touched: set[str] = set()
        for approval in expired:
            touched.add(approval.session_id)
            await self._record(
                approval.session_id,
                ApprovalResolved(
                    approval_id=approval.approval_id,
                    choice=approval.choice or "deny",
                    status="timed_out",
                ),
                turn_id=approval.turn_id,
                trace_id=self._trace_for(approval.session_id),
            )
            # Warm harness UI reply when timeout allows.
            if is_allow_choice(approval.choice):
                runtime = self._registry.get(approval.session_id)
                if runtime is not None and approval.harness_request_id:
                    send_ui = getattr(runtime, "respond_ui", None)
                    if send_ui is not None:
                        await send_ui(
                            harness_request_id=approval.harness_request_id,
                            choice=approval.choice,
                        )
        for sid in touched:
            remaining = await self._approvals.pending(sid)
            if remaining:
                continue
            async with self._db() as db:
                row = await db.get(SessionRow, sid)
                if row is not None and row.status == "awaiting_approval":
                    row.status = "active"
                    await db.commit()
        return expired

    def check_tool_approval_required(self, session_id: str, name: str) -> bool:
        """True when harness ``require_approval`` lists ``name`` for this session."""
        return self.require_approval_for_tool(session_id, name)

    def require_approval_for_tool(self, session_id: str, name: str) -> bool:
        """True when named tool must park before provider.invoke."""
        manifest = self._approval_manifest_cache.get(session_id)
        if manifest is None:
            return False
        if manifest.approvals is None:
            return False
        return name in manifest.approvals.require_approval

    async def _resolve_require_approval(
        self, session_id: str, name: str
    ) -> bool:
        row = await self._get_session_row(session_id)
        manifest = self._pinned_manifest(row)
        self._approval_manifest_cache[session_id] = manifest
        if manifest.approvals is None:
            return False
        return name in manifest.approvals.require_approval

    async def _harness_request_allowed(
        self, session_id: str, harness_request_id: str
    ) -> bool:
        from supervisor.policy.types import is_allow_choice

        prior = await self._approvals.get_by_harness_request(
            session_id, harness_request_id
        )
        if prior is None:
            return False
        if prior.status == "responded" and is_allow_choice(prior.choice):
            return True
        if prior.status == "timed_out" and is_allow_choice(prior.choice):
            return True
        return False

    async def invoke_tool(
        self,
        session_id: str,
        *,
        tenant_id: str,
        name: str,
        arguments: dict[str, Any],
        tool_call_id: str | None = None,
    ) -> ToolInvokeOutcome:
        row = await self._require_active(session_id)
        if row.tenant_id != tenant_id:
            raise SessionNotFound(f"unknown session_id={session_id!r}")
        ws = self._workspace.get(session_id)
        if ws is not None:
            self._workspace_svc.assert_ready(ws)
        seams = self._seams_for_session(session_id, row)
        if seams is None or seams.tool_provider is None:
            raise ToolInvokeNotConfigured(
                f"session {session_id!r} has no composition tool provider"
            )
        call_id = tool_call_id or uuid4().hex
        denial = self.tool_denial(session_id, name, seams.tool_provider)
        run_trace = self._trace_for(session_id)
        if denial is not None:
            result = ToolResult(ok=False, error=denial, code="FILE_EFFECTS_DENIED")
            await self._record(
                session_id,
                ToolStart(tool_call_id=call_id, tool_name=name, args=dict(arguments)),
                turn_id=None,
                trace_id=run_trace,
            )
            await self._record(
                session_id,
                ToolEnd(
                    tool_call_id=call_id,
                    tool_name=name,
                    result={"error": denial, "code": "FILE_EFFECTS_DENIED"},
                    is_error=True,
                ),
                turn_id=None,
                trace_id=run_trace,
            )
            return ToolInvokeOutcome(tool_call_id=call_id, result=result)

        # Pre-invoke require_approval gate (before outbox / provider body).
        if await self._resolve_require_approval(session_id, name):
            if not await self._harness_request_allowed(session_id, call_id):
                manifest = self._pinned_manifest(row)
                timeout = (
                    manifest.approvals.timeout_s
                    if manifest.approvals is not None
                    else 300.0
                )
                payload, parked = await self._handle_approval_required(
                    session_id,
                    None,
                    ApprovalRequired(
                        approval_id="",
                        prompt=f"Approval required to invoke tool {name!r}",
                        options=["approve", "deny"],
                        timeout_s=timeout,
                        harness_request_id=call_id,
                    ),
                )
                if parked:
                    await self._record(
                        session_id,
                        payload,
                        turn_id=None,
                        trace_id=run_trace,
                    )
                    raise SessionAwaitingApproval(
                        f"session awaiting approval id={session_id!r} tool={name!r}"
                    )

        ctx = ToolInvokeContext(trace_id=call_id, extra=self.tool_context(session_id, tenant_id))
        begin = await self._tool_outbox.begin(
            session_id,
            tool_call_id=call_id,
            tool_name=name,
            arguments=dict(arguments),
            trace_id=run_trace,
        )
        if begin.action == "cached" and begin.cached is not None:
            return ToolInvokeOutcome(tool_call_id=call_id, result=begin.cached)

        # Tools block (files, sandboxes, HTTP); off the loop so the worker's heartbeats
        # and every other session it holds keep running.
        result = await asyncio.to_thread(seams.tool_provider.invoke, ctx, name, arguments)
        result = await self._tool_outbox.complete(
            session_id,
            tool_call_id=call_id,
            tool_name=name,
            result=result,
            trace_id=run_trace,
        )
        return ToolInvokeOutcome(tool_call_id=call_id, result=result)


    async def _trusted_parent_depth(self, session_id: str, claimed: int) -> int:
        """Server-side parent_depth (VibeSec H1).

        Walk journal ancestry via SubagentStart(child→parent). Ignore client
        understatement; honor higher claims for over-depth probes (T5).
        Nested ACP must spawn on the child session so ancestry is visible.
        Product one-shot from a root session derives depth 0 from the tree.
        """
        derived = 0
        current = session_id
        seen: set[str] = set()
        parent_of: dict[str, str] = {}
        try:
            async with self._db() as db:
                rows = (
                    await db.execute(
                        select(EventRow).where(EventRow.type == "SubagentStart")
                    )
                ).scalars().all()
            for row in rows:
                payload = row.payload or {}
                child = payload.get("child_session_id")
                parent = payload.get("parent_session_id")
                if child and parent and child not in parent_of:
                    parent_of[str(child)] = str(parent)
            while current in parent_of and current not in seen and derived < 64:
                seen.add(current)
                current = parent_of[current]
                derived += 1
        except Exception:
            derived = 0
        try:
            envs = await self._reader.read(session_id, since=-1)
            _ = build_session_tree(session_id, envs)
        except Exception:
            pass
        claimed_i = max(int(claimed or 0), 0)
        return max(derived, claimed_i)

    async def spawn_child(
        self,
        session_id: str,
        *,
        tenant_id: str,
        provider: str | None,
        request: SubagentRequest,
        turn_id: str | None = None,
        trace_id: str | None = None,
    ):
        row = await self._require_active(session_id)
        if row.tenant_id != tenant_id:
            raise SessionNotFound(f"unknown session_id={session_id!r}")
        provider = self.subagent_provider(session_id, row, provider)
        request.parent_depth = await self._trusted_parent_depth(
            session_id, request.parent_depth
        )
        ops = self._subagent_ops(session_id, row)
        return await ops.spawn(
            provider,
            request,
            turn_id=turn_id,
            trace_id=trace_id,
        )

    async def fork_child(
        self,
        session_id: str,
        *,
        tenant_id: str,
        provider: str | None,
        request: SubagentRequest,
        turn_id: str | None = None,
        trace_id: str | None = None,
    ):
        row = await self._require_active(session_id)
        if row.tenant_id != tenant_id:
            raise SessionNotFound(f"unknown session_id={session_id!r}")
        provider = self.subagent_provider(session_id, row, provider)
        request.parent_depth = await self._trusted_parent_depth(
            session_id, request.parent_depth
        )
        ops = self._subagent_ops(session_id, row)
        return await ops.fork(
            provider,
            request,
            turn_id=turn_id,
            trace_id=trace_id,
        )

    async def begin_subagent_background(
        self,
        session_id: str,
        *,
        tenant_id: str,
        provider: str | None,
        request: SubagentRequest,
        job_id: str,
        run_id: str,
        turn_id: str | None = None,
        trace_id: str | None = None,
    ) -> JobHandle:
        """Validate and journal a background job; its work runs as its own durable job."""
        row = await self._require_active(session_id)
        if row.tenant_id != tenant_id:
            raise SessionNotFound(f"unknown session_id={session_id!r}")
        provider = self.subagent_provider(session_id, row, provider)
        request.parent_depth = await self._trusted_parent_depth(
            session_id, request.parent_depth
        )
        return await self._subagent_ops(session_id, row).begin_background(
            provider, request, job_id=job_id, run_id=run_id, turn_id=turn_id, trace_id=trace_id
        )

    async def subagent_job_ops(
        self, session_id: str, request: SubagentRequest
    ) -> SubagentOps:
        """The session's subagent transport, on any worker (the session need not be live).

        ``request.parent_depth`` is re-derived from the journal, never trusted.
        """
        row = await self._get_session_row(session_id)
        request.parent_depth = await self._trusted_parent_depth(
            session_id, request.parent_depth
        )
        return self._subagent_ops(session_id, row)

    async def start_subagent_continuable(
        self,
        session_id: str,
        *,
        tenant_id: str,
        provider: str | None,
        request: SubagentRequest,
        turn_id: str | None = None,
        trace_id: str | None = None,
    ):
        row = await self._require_active(session_id)
        if row.tenant_id != tenant_id:
            raise SessionNotFound(f"unknown session_id={session_id!r}")
        provider = self.subagent_provider(session_id, row, provider)
        request.parent_depth = await self._trusted_parent_depth(
            session_id, request.parent_depth
        )
        ops = self._subagent_ops(session_id, row)
        return await ops.start_continuable(
            provider,
            request,
            turn_id=turn_id,
            trace_id=trace_id,
        )

    async def send_child_message(
        self,
        session_id: str,
        *,
        tenant_id: str,
        child_id: str,
        text: str,
    ) -> None:
        row = await self._require_active(session_id)
        if row.tenant_id != tenant_id:
            raise SessionNotFound(f"unknown session_id={session_id!r}")
        ops = self._subagent_ops(session_id, row)
        ops.send_child_message(child_id, text)

    async def interrupt_child(
        self,
        session_id: str,
        *,
        tenant_id: str,
        child_id: str,
    ) -> None:
        row = await self._require_active(session_id)
        if row.tenant_id != tenant_id:
            raise SessionNotFound(f"unknown session_id={session_id!r}")
        ops = self._subagent_ops(session_id, row)
        ops.interrupt_child(child_id)

    async def list_children(
        self,
        session_id: str,
        *,
        tenant_id: str,
        scope: str = "direct",
    ) -> list[ChildRow]:
        row = await self._require_active(session_id)
        if row.tenant_id != tenant_id:
            raise SessionNotFound(f"unknown session_id={session_id!r}")
        tree = await self._reader.session_tree(session_id)
        return tree.list_children(scope=scope)

    def _memory_ops(self, session_id: str, row: SessionRow) -> MemoryOps:
        seams = self._seams_for_session(session_id, row) or self._fallback_seams
        memory = seams.context_memory
        if memory is None:
            raise MemoryNotConfigured(
                f"session {session_id!r} has no ContextMemoryProvider configured"
            )
        ws = session_workspace(session_id)
        return MemoryOps(
            session_id=session_id,
            memory=memory,
            writer=self._writer,
            tenant_id=row.tenant_id,
            workspace_path=str(ws),
        )

    async def compact_memory(
        self,
        session_id: str,
        *,
        tenant_id: str,
        preparation: CompactionPreparation,
        reason: Literal["manual", "threshold", "overflow"],
    ) -> CompactMemoryOutcome:
        row = await self._require_active(session_id)
        if row.tenant_id != tenant_id:
            raise SessionNotFound(f"unknown session_id={session_id!r}")
        ops = self._memory_ops(session_id, row)
        return await ops.compact(preparation, reason=reason)

    async def recall_memory(
        self,
        session_id: str,
        *,
        tenant_id: str,
        user_prompt: str,
    ) -> RecallMemoryOutcome:
        row = await self._require_active(session_id)
        if row.tenant_id != tenant_id:
            raise SessionNotFound(f"unknown session_id={session_id!r}")
        ops = self._memory_ops(session_id, row)
        return await ops.recall(user_prompt)

    def _subagent_ops(self, session_id: str, row: SessionRow) -> SubagentOps:
        seams = self._seams_for_session(session_id, row) or self._fallback_seams
        transport = seams.subagent_transport
        if transport is None:
            raise SubagentNotConfigured(
                f"session {session_id!r} has no SubagentTransport configured"
            )
        manifest = self._pinned_manifest(row)
        return SubagentOps(
            parent_session_id=session_id,
            transport=transport,
            writer=self._writer,
            max_depth=getattr(manifest, "max_subagent_depth", 3),
        )

    async def resolve_subagent_provider(self, session_id: str, requested: str | None) -> str:
        return self.subagent_provider(session_id, await self._get_session_row(session_id), requested)

    def subagent_provider(self, session_id: str, row: SessionRow, requested: str | None) -> str:
        """``requested``, or the session's pinned transport default when none is named."""
        if requested:
            return requested
        seams = self._seams_for_session(session_id, row) or self._fallback_seams
        default = getattr(seams.subagent_transport, "default_provider", None)
        if not default:
            raise SubagentNotConfigured(f"session {session_id!r} has no subagent provider")
        return str(default)

    def _seams_for_session(self, session_id: str, row: SessionRow) -> RuntimeSeams | None:
        """The seams the session's runtime holds: one set for every path that uses them."""
        live = getattr(self._registry.get(session_id), "seams", None)
        if live is not None:
            return live
        cached = self._seams.get(session_id)
        if cached is not None:
            return cached
        if row.composition_pins and self._composition_catalog is not None:
            pins = SessionPins.from_dict(dict(row.composition_pins))
            seams = bind_runtime_seams(
                pins,
                self._composition_catalog,
                session_id=session_id,
                model=session_model(self._pinned_manifest(row), pins),
            )
            self._seams[session_id] = seams
            return seams
        return None

    def _sandbox_profile_for_row(self, row: SessionRow):
        if row.composition_pins and self._composition_catalog is not None:
            pins = SessionPins.from_dict(dict(row.composition_pins))
            return load_pinned_sandbox_profile(pins, self._composition_catalog)
        return None

    def _configure_policy(
        self,
        session_id: str,
        manifest: HarnessManifest,
        *,
        sandbox_profile: Any | None = None,
    ) -> None:
        self._budget.configure(session_id, manifest.budget)
        g = manifest.guards or GuardSpec()
        self._guards.configure(
            session_id,
            GuardConfig(
                repetition=GuardAction(g.repetition),
                oscillation=GuardAction(g.oscillation),
                stall_iterations=g.stall_iterations,
            ),
        )
        sandbox = self._sandboxes.get(session_id)
        self._sandbox_policy.configure(
            session_id,
            sandbox_profile,
            isolated=sandbox is not None and sandbox.info.provider in ISOLATING_PROVIDERS,
        )

    async def _handle_approval_required(
        self,
        session_id: str,
        turn_id: str | None,
        payload: ApprovalRequired,
    ) -> tuple[ApprovalRequired, bool]:
        row = await self._get_session_row(session_id)
        manifest = self._pinned_manifest(row)
        timeout = payload.timeout_s
        if manifest.approvals is not None:
            timeout = manifest.approvals.timeout_s or timeout
        auto_choice = self._sandbox_policy.auto_approve_choice(
            session_id, list(payload.options)
        )
        approval_id = await self._approvals.request(
            session_id,
            turn_id,
            payload.prompt,
            payload.options,
            timeout,
            harness_request_id=payload.harness_request_id,
        )
        if auto_choice is not None:
            await self._approvals.respond(approval_id, auto_choice)
            resolved = ApprovalResolved(
                approval_id=approval_id,
                choice=auto_choice,
                status="responded",
            )
            await self._record(
                session_id,
                resolved,
                turn_id=turn_id,
                trace_id=self._trace_for(session_id),
            )
            return (
                ApprovalRequired(
                    approval_id=approval_id,
                    prompt=payload.prompt,
                    options=payload.options,
                    timeout_s=timeout,
                    harness_request_id=payload.harness_request_id,
                ),
                False,
            )
        async with self._db() as db:
            srow = await db.get(SessionRow, session_id)
            if srow is not None:
                srow.status = "awaiting_approval"
                await db.commit()
        return (
            ApprovalRequired(
                approval_id=approval_id,
                prompt=payload.prompt,
                options=payload.options,
                timeout_s=timeout,
                harness_request_id=payload.harness_request_id,
            ),
            True,
        )

    async def _record(
        self,
        session_id: str,
        payload: Any,
        *,
        turn_id: str | None,
        trace_id: str | None,
        event_key: str | None = None,
        event_id: str | None = None,
        ts_ms: int | None = None,
    ) -> int:
        """Journal one event and apply its effects; the single path every event takes.

        Workspace effects run before the append (files are synced before clients see
        RunEnd); then the event is published live and projected into tracing. With
        ``event_key`` the append is idempotent (retried workflow activities).
        """
        await self._workspace_effects(session_id, payload, trace_id=trace_id)
        seq = await self._writer.append(
            session_id, payload, turn_id=turn_id, trace_id=trace_id, event_key=event_key
        )
        envelope = Envelope(
            session_id=session_id,
            seq=seq,
            event_id=event_id or uuid4().hex,
            ts_ms=ts_ms if ts_ms is not None else int(time.time() * 1000),
            turn_id=turn_id,
            trace_id=trace_id,
            payload=payload,
        )
        self._live.publish(session_id, envelope)
        await self._project(session_id, envelope)
        return seq

    async def _workspace_effects(
        self, session_id: str, payload: Any, *, trace_id: str | None
    ) -> None:
        if isinstance(payload, RunStart) and trace_id:
            self._run_traces[session_id] = trace_id
        ws = self._workspace.get(session_id)
        if (
            isinstance(payload, ToolEnd)
            and not payload.is_error
            and payload.tool_name in _MUTATING_TOOLS
            and ws is not None
        ):
            self._workspace_svc.mark_dirty(ws)
        if isinstance(payload, RunEnd):
            self._run_traces.pop(session_id, None)
            sbx = self._sandboxes.get(session_id)
            if ws is not None and ws.dirty and sbx is not None:
                try:
                    await self._workspace_svc.sync_if_dirty(ws, sbx)
                except WorkspaceConflictError:
                    _log.exception("workspace CAS conflict session=%s", session_id)
                except Exception:
                    _log.exception("workspace sync after run failed session=%s", session_id)

    async def _project(self, session_id: str, envelope: Envelope) -> None:
        """Project a journalled event into tracing spans (D13); never fails the event."""
        try:
            from supervisor.obs.tracing import get_projector

            proj = get_projector()
            if proj is None:
                return
            if hasattr(proj, "ensure_session"):
                row = await self._get_session_row(session_id)
                harness_ver = getattr(row, "harness_version", None) or "unknown"
                # Prefer the composition id when pinned; else the harness version.
                pins = getattr(row, "composition_pins", None)
                harness_id = (
                    str(pins["composition_id"])
                    if isinstance(pins, dict) and pins.get("composition_id")
                    else harness_ver
                )
                proj.ensure_session(
                    session_id,
                    tenant_id=getattr(row, "tenant_id", None),
                    harness_id=harness_id,
                    harness_version=harness_ver,
                )
            proj.project(envelope)
        except Exception:
            _log.exception("span projection failed session=%s", session_id)

    async def _policy_abort(
        self,
        session_id: str,
        runtime: Any,
        *,
        error_class: str,
        detail: str,
        status: str = "failed",
    ) -> None:
        try:
            await runtime.abort()
        except Exception:
            _log.exception("abort failed session=%s", session_id)
        # Status first: anyone reacting to the ErrorEvent must already see it closed.
        async with self._db() as db:
            row = await db.get(SessionRow, session_id)
            if row is not None:
                row.status = status
                await db.commit()
        await self._record(
            session_id,
            ErrorEvent(error_class=error_class, retryable=False, detail=detail),
            turn_id=None,
            trace_id=self._trace_for(session_id, runtime),
        )
        try:
            # Stops the runtime and releases its sandbox; the session row keeps its status.
            await self._stop_runtime_keep_session(session_id)
        except Exception:
            _log.exception("cleanup after policy abort failed session=%s", session_id)
        self._run_traces.pop(session_id, None)

    async def _stop_runtime_keep_session(self, session_id: str) -> None:
        runtime = self._registry.remove(session_id)
        if runtime is not None:
            await runtime.stop()
        sandbox = self._sandboxes.pop(session_id, None)
        if sandbox is not None:
            await sandbox.destroy()

    async def _get_session_row(self, session_id: str) -> SessionRow:
        async with self._db() as db:
            row = await db.get(SessionRow, session_id)
            if row is None:
                raise SessionNotFound(f"unknown session_id={session_id!r}")
            return row

    async def _require_active(self, session_id: str) -> SessionRow:
        row = await self._get_session_row(session_id)
        if row.status == "terminated":
            raise SessionTerminated(f"session terminated id={session_id!r}")
        if row.status == "awaiting_approval":
            raise SessionAwaitingApproval(
                f"session awaiting approval id={session_id!r}"
            )
        if row.status == "failed":
            raise SessionTerminated(f"session failed id={session_id!r}")
        if row.status == "budget_exceeded":
            self._configure_policy(
                session_id,
                self._pinned_manifest(row),
                sandbox_profile=self._sandbox_profile_for_row(row),
            )
            raise BudgetExceeded.from_state(await self._budget.check(session_id))
        # Ensure limits are loaded after restart / cold path.
        self._configure_policy(
            session_id,
            self._pinned_manifest(row),
            sandbox_profile=self._sandbox_profile_for_row(row),
        )
        return row

    async def event_payloads(self, session_id: str) -> list[str]:
        """Test helper: payload type names in journal order."""
        envs = await self._reader.read(session_id, since=-1)
        return [type(e.payload).__name__ for e in envs]

    async def snapshot_events(self, session_id: str) -> list[tuple[int, str]]:
        async with self._db() as db:
            rows = (
                await db.execute(
                    select(EventRow)
                    .where(EventRow.session_id == session_id)
                    .order_by(EventRow.seq.asc())
                )
            ).scalars().all()
        return [(int(r.seq), r.event_id) for r in rows]


def _summarizer_model(profile: MemoryProfile | None) -> str | None:
    """The model a session's compaction summarizes with, when it names its own."""
    if profile is None or not profile.compaction_enabled:
        return None
    return profile.summarization_model or None
