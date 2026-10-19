"""State every layer of the session manager shares, and the helpers they all use:"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.agent.events import (
    Envelope,
    ErrorEvent,
    RunEnd,
    RunStart,
    ToolEnd,
)
from supervisor.agent.seams import RuntimeSeams
from supervisor.catalog import MemoryProfile, SessionPins
from supervisor.catalog.core.types import FileEffects
from supervisor.catalog.providers.binder import bind_runtime_seams
from supervisor.catalog.providers.memory_resolve import load_pinned_memory_profile
from supervisor.catalog.storage import CompositionCatalog
from supervisor.db.models import EventRow, SessionRow
from supervisor.harness import (
    GuardSpec,
    HarnessCatalog,
    HarnessManifest,
    manifest_from_dict,
)
from supervisor.journal.reader import JournalReader
from supervisor.journal.writer import JournalWriter
from supervisor.memory.platform import MemoryPlatform
from supervisor.models.browser import BrowserAccess
from supervisor.policy.approvals import ApprovalBroker
from supervisor.policy.budget import BudgetExceeded, BudgetTracker
from supervisor.policy.guards import GuardAction, GuardConfig, LoopGuard
from supervisor.policy.quotas import TenantQuotaEnforcer
from supervisor.policy.sandbox_policy import ISOLATING_PROVIDERS, SandboxPolicy
from supervisor.sandbox.base import Sandbox, SandboxProvider
from supervisor.sandbox.resolve import load_pinned_sandbox_profile
from supervisor.seams import (
    ToolResult,
    build_runtime_seams,
)
from supervisor.seams.services import (
    AuthzService,
    GrantStore,
    SessionWorkspaceService,
    SessionWorkspaceState,
    VirtualKeyClient,
    WorkspaceConflictError,
)
from supervisor.session.errors import (
    SessionAwaitingApproval,
    SessionNotFound,
    SessionTerminated,
)
from supervisor.session.live import LiveHub, LiveSubscription
from supervisor.session.modes import SessionMode
from supervisor.session.recovery import Recovery, ResumeState
from supervisor.session.registry import SessionRegistry
from supervisor.session.tool_outbox import ToolOutbox

_log = logging.getLogger(__name__)
_LIVE_QUEUE_MAX = 256
_MUTATING_TOOLS = frozenset({"write", "edit", "edit_diff"})



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


def _summarizer_model(profile: MemoryProfile | None) -> str | None:
    """The model a session's compaction summarizes with, when it names its own."""
    if profile is None or not profile.compaction_enabled:
        return None
    return profile.summarization_model or None


class SessionManagerBase:
    """State every layer of the session manager shares, and the helpers they all use:
    the journal path every event takes, row lookups, seams, policy wiring."""

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

    @property
    def memory(self) -> MemoryPlatform:
        return self._memory

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

    def _pinned_manifest(self, row: SessionRow) -> HarnessManifest:
        """Resume/recovery always use the snapshot stored on the session."""
        if row.manifest:
            return manifest_from_dict(dict(row.manifest))
        return self._catalog.resolve_sync(row.harness_version, for_create=False)

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
        await self._workspace_effects(
            session_id, payload, trace_id=trace_id, turn_id=turn_id, event_key=event_key
        )
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
        self,
        session_id: str,
        payload: Any,
        *,
        trace_id: str | None,
        turn_id: str | None = None,
        event_key: str | None = None,
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
                except WorkspaceConflictError as conflict:
                    await self._workspace_conflict(
                        session_id, ws, sbx, conflict, turn_id=turn_id, trace_id=trace_id,
                        event_key=f"{event_key}:workspace-conflict" if event_key else None,
                    )
                except Exception:
                    _log.exception("workspace sync after run failed session=%s", session_id)

    async def _workspace_conflict(
        self,
        session_id: str,
        ws: SessionWorkspaceState,
        sandbox: Sandbox,
        conflict: WorkspaceConflictError,
        *,
        turn_id: str | None,
        trace_id: str | None,
        event_key: str | None,
    ) -> None:
        """Another session changed these files first: the scope keeps its version, the
        sandbox is refreshed from it, and the user is told which paths (F20)."""
        paths = ", ".join(conflict.conflicts)
        _log.warning("workspace conflict session=%s paths=%s", session_id, paths)
        try:
            await self._workspace_svc.recover_cas_conflict(ws, sandbox)
            refreshed = "this session's workspace now holds the scope's current files"
        except Exception:
            _log.exception("workspace refresh after a conflict failed session=%s", session_id)
            refreshed = "refreshing this session's workspace failed; rehydrate it"
        await self._record(
            session_id,
            ErrorEvent(
                error_class="WorkspaceConflict",
                retryable=False,
                detail=f"not saved, changed in the scope by someone else first: {paths}; {refreshed}",
                error_code="WORKSPACE_CONFLICT",
            ),
            turn_id=turn_id,
            trace_id=trace_id,
            event_key=event_key,
        )

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

    async def _stop_runtime_keep_session(self, session_id: str) -> None:
        runtime = self._registry.remove(session_id)
        if runtime is not None:
            await runtime.stop()
        sandbox = self._sandboxes.pop(session_id, None)
        if sandbox is not None:
            await sandbox.destroy()

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

    async def session_status(self, session_id: str) -> str | None:
        async with self._db() as db:
            row = await db.get(SessionRow, session_id)
            return None if row is None else str(row.status)
