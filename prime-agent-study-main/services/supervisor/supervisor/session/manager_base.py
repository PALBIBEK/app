"""State every layer of the session manager shares, and the helpers they all use:"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.agent.events import (
    Envelope,
    ErrorEvent,
    HandCheckpointed,
    HandLost,
    HandProvisioned,
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
from supervisor.db.models import EventRow, ScopeRow, SessionRow
from supervisor.hands import SessionHands
from supervisor.hands.checkpoint import Checkpointer, checkpoint_scope
from supervisor.hands.inspect import Changes
from supervisor.hands.keys import DEFAULT_HAND, split_key
from supervisor.hands.leases import HandLeases
from supervisor.hands.lifecycle import SandboxLifecycle
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
    WorkspaceStore,
)
from supervisor.seams.services.virtual_keys import MintedVirtualKey
from supervisor.session.errors import (
    SessionAwaitingApproval,
    SessionNotFound,
    SessionTerminated,
)
from supervisor.session.files import SessionFiles
from supervisor.session.live import LiveHub, LiveSubscription
from supervisor.session.modes import SessionMode
from supervisor.session.recovery import Recovery, ResumeState
from supervisor.session.registry import SessionRegistry
from supervisor.session.tool_outbox import ToolOutbox
from supervisor.vault import Vault
from supervisor.vault.credentials import ToolCredentials
from supervisor.vault.session_keys import SessionKeys

logger = logging.getLogger(__name__)
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
        workspace_store: WorkspaceStore | None = None,
        vault: Vault | None = None,
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
        self._rollups: set[asyncio.Task[Any]] = set()
        """Trace rollups being written (``supervisor.traces``); held so none is collected mid-write."""
        self._hands = SessionHands(_HandHooks(self))
        # When sandboxes start and stop, where a profile does not say (supervisor.hands.lifecycle).
        self.sandbox_lifecycle = SandboxLifecycle.from_env()
        self._leases = HandLeases(session_factory)  # who else holds a hand, if anyone
        # Change-aware checkpoints of each sandbox, where there is a store to keep them.
        self._checkpoints = (
            Checkpointer(workspace_store, record=self._journal_checkpoint)
            if workspace_store is not None
            else None
        )
        self._checkpoint_scopes: set[str] = set()
        # Attachments, run inputs and artifacts (supervisor.session.files), with a store.
        self.files: SessionFiles | None = (
            SessionFiles(session_factory, workspace_store) if workspace_store is not None else None
        )
        self._creating: set[str] = set()  # sessions whose journal row does not exist yet
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
        # Each session's gateway key lives in the vault, so every worker shares it.
        self._session_keys = SessionKeys(vault) if vault is not None else None
        # Tenant secrets that authenticate tool servers, sent by the supervisor itself.
        self._credentials = ToolCredentials(vault) if vault is not None else None
        self._tenant_of: dict[str, str] = {}  # live sessions and brains: whose secrets
        self._key_mints: dict[str, Callable[[], MintedVirtualKey]] = {}
        self._key_expiry: dict[str, float] = {}  # this worker's key, by session: when it expires
        # Brains of sessions whose sandbox another worker holds (model steps run anywhere).
        self._brains: OrderedDict[str, Any] = OrderedDict()
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

    @property
    def harness_catalog(self) -> HarnessCatalog:
        """The harness versions sessions are created on."""
        return self._catalog

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
        recipe = self._hands.recipe(session_id)
        self._sandbox_policy.configure(
            session_id,
            sandbox_profile,
            isolated=recipe is not None and recipe.provider_name in ISOLATING_PROVIDERS,
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
        if turn_id is not None and isinstance(payload, (RunStart, RunEnd)):
            self._record_trace(session_id, turn_id, ended=isinstance(payload, RunEnd))
        return seq

    def _trace_model_client(self, row: SessionRow) -> Any:
        """The session's own model client, for judging and summarising its runs."""
        client_for = getattr(self, "session_model_client", None)
        return client_for(row.id, row) if callable(client_for) else None

    def _record_trace(self, session_id: str, run_id: str, *, ended: bool = False) -> None:
        """Refresh the run's trace rollup beside the event path, never on it: tracing must not
        slow a run. A rollup lost to a crash is rebuilt the next time the run is read."""
        from supervisor.traces.after_run import after_run  # traces reads sessions: import late
        from supervisor.traces.store import record_run

        work: Coroutine[Any, Any, Any] = (
            after_run(self._db, self._reader, session_id, run_id, self._trace_model_client)
            if ended
            else record_run(self._db, self._reader, session_id, run_id)
        )
        task: asyncio.Task[Any] = asyncio.create_task(work)
        self._rollups.add(task)
        task.add_done_callback(self._rollups.discard)

    async def drain_trace_rollups(self) -> None:
        """Wait for the trace rollups being written (tests, and a clean shutdown)."""
        if self._rollups:
            await asyncio.gather(*self._rollups, return_exceptions=True)

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
        if isinstance(payload, RunStart) and self.files is not None and turn_id:
            await self._run_starts(session_id, turn_id, ws)
        if (
            isinstance(payload, ToolEnd)
            and not payload.is_error
            and payload.tool_name in _MUTATING_TOOLS
            and ws is not None
        ):
            self._workspace_svc.mark_dirty(ws)
        if isinstance(payload, RunEnd):
            self._run_traces.pop(session_id, None)
            sbx = self._hands.sandbox(session_id)
            if self.files is not None and sbx is not None and turn_id:
                try:
                    await self.files.collect(session_id, turn_id, sbx)
                except Exception:
                    logger.exception("collecting the run's outputs failed session=%s", session_id)
            if ws is not None and ws.dirty and sbx is not None:
                try:
                    await self._workspace_svc.sync_if_dirty(
                        ws, sbx,
                        author=ws.principal.id if ws.principal is not None else None,
                        source=f"run:{session_id}/{turn_id}" if turn_id else f"run:{session_id}",
                    )
                except WorkspaceConflictError as conflict:
                    await self._workspace_conflict(
                        session_id, ws, sbx, conflict, turn_id=turn_id, trace_id=trace_id,
                        event_key=f"{event_key}:workspace-conflict" if event_key else None,
                    )
                except Exception:
                    logger.exception("workspace sync after run failed session=%s", session_id)

    async def _run_starts(
        self, session_id: str, turn_id: str, ws: SessionWorkspaceState | None
    ) -> None:
        """A run starts: record what it starts from, and give a live sandbox its attachments."""
        assert self.files is not None
        try:
            await self.files.snapshot(session_id, turn_id, ws.scope_id if ws is not None else None)
            if (sbx := self._hands.sandbox(session_id)) is not None:
                await self.files.deliver(session_id, sbx)
        except Exception:
            logger.exception("recording the run's inputs failed session=%s", session_id)

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
        logger.warning("workspace conflict session=%s paths=%s", session_id, paths)
        try:
            await self._workspace_svc.recover_cas_conflict(ws, sandbox)
            refreshed = "this session's workspace now holds the scope's current files"
        except Exception:
            logger.exception("workspace refresh after a conflict failed session=%s", session_id)
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
            logger.exception("span projection failed session=%s", session_id)

    async def _announce_hand(self, key: str, sandbox: Sandbox, cause: str) -> None:
        """Journal that ``sandbox`` serves one of the session's hands (once per sandbox)."""
        session_id, hand = split_key(key)
        await self._record(
            session_id,
            HandProvisioned(
                hand_id=sandbox.id, provider=sandbox.info.provider, cause=cause, hand=hand
            ),
            turn_id=None,
            trace_id=self._trace_for(session_id),
            event_key=f"hand:{sandbox.id}:provisioned",
        )

    def _hand_isolated(self, key: str) -> bool:
        """Whether a hand's sandboxes run in their own container (processes observable)."""
        recipe = self._hands.recipe(key)
        return recipe is not None and recipe.provider_name in ISOLATING_PROVIDERS

    async def checkpoint(self, session_id: str) -> None:
        """Save what the session's tools changed in each of its hands, if anything.

        Runs while the model works on its next step. A failure only means this step
        is not saved: the next step tries again, with everything still pending.
        """
        if self._checkpoints is None:
            return
        for key in self._hands.keys_of(session_id) or [session_id]:
            sandbox = self._hands.sandbox(key)
            if sandbox is None or self._checkpoints.due(key) is None:
                continue
            try:
                await self._ensure_checkpoint_scope(key)
                await self._checkpoints.checkpoint(key, sandbox)
            except Exception:
                logger.exception("checkpoint failed hand=%s", key)

    async def _ensure_checkpoint_scope(self, key: str) -> None:
        """A hand's checkpoint scope exists and belongs to the session's owner."""
        scope_id = checkpoint_scope(key)
        if scope_id in self._checkpoint_scopes:
            return
        session_id, _ = split_key(key)
        row = await self._get_session_row(session_id)
        owner = row.owner_principal_id or row.tenant_id
        async with self._db() as db:
            if await db.get(ScopeRow, scope_id) is None:
                db.add(
                    ScopeRow(
                        id=scope_id,
                        type="session",
                        owner_principal_id=owner,
                        name=f"checkpoints of hand {key}",
                    )
                )
                await db.commit()
                if self._grant_store is not None:
                    await self._grant_store.bootstrap_owner(owner, scope_id)
        self._checkpoint_scopes.add(scope_id)

    async def _has_checkpoint(self, key: str) -> bool:
        session_id, hand = split_key(key)
        return any(
            isinstance(e.payload, HandCheckpointed) and e.payload.hand == hand
            for e in await self._reader.read(session_id, since=-1)
        )

    async def _journal_checkpoint(
        self, key: str, sandbox: Sandbox, changes: Changes, processes: frozenset[str]
    ) -> None:
        session_id, hand = split_key(key)
        await self._record(
            session_id,
            HandCheckpointed(
                hand_id=sandbox.id,
                files=len(changes.written),
                removed=len(changes.removed),
                processes=sorted(processes),
                hand=hand,
            ),
            turn_id=None,
            trace_id=self._trace_for(session_id),
        )

    async def _stop_runtime_keep_session(self, session_id: str) -> None:
        runtime = self._registry.remove(session_id)
        if runtime is not None:
            await runtime.stop()
        sandbox = self._hands.detach(session_id)
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
        """The session's status; a hand's key is its session's (the orphan reaper asks by label)."""
        session_id, _ = split_key(session_id)
        async with self._db() as db:
            row = await db.get(SessionRow, session_id)
            return None if row is None else str(row.status)


class _HandHooks:
    """The session layer around a sandbox's life: its files and its journal entries."""

    __slots__ = ("_m",)

    def __init__(self, manager: SessionManagerBase) -> None:
        self._m = manager

    async def hydrate(self, key: str, sandbox: Sandbox, *, adopted: bool = False) -> None:
        """A new sandbox starts empty: load the hand's last checkpoint, else the session's scope.

        Only the default hand mirrors the session's scope; a named hand starts empty.
        An adopted sandbox (a dead worker's, still alive) already holds the newest files:
        restoring the checkpoint over it would undo every tool that ended after it, while
        the journal says they ran. It is observed instead, so the next checkpoint saves it.
        """
        m = self._m
        session_id, hand = split_key(key)
        state = m._workspace.get(session_id) if hand == DEFAULT_HAND else None
        if adopted:
            if m._checkpoints is not None:
                await m._checkpoints.observe(key, sandbox, processes=m._hand_isolated(key))
            if state is not None:
                state.ready = True
            return
        if m._checkpoints is not None and await m._has_checkpoint(key):
            await m._checkpoints.restore(key, sandbox, processes=m._hand_isolated(key))
            if state is not None and state.scope_id is not None:
                state.ready = True  # the checkpoint mirrors the scope's files and the edits since
            if hand == DEFAULT_HAND and m.files is not None:
                await m.files.deliver(session_id, sandbox)
            return
        if state is not None and state.scope_id is not None:
            await m._workspace_svc.ensure_hydrated(state, sandbox, force=True)
        if hand == DEFAULT_HAND and m.files is not None:
            await m.files.deliver(session_id, sandbox)  # this session's attachments, read-only

    async def provisioned(self, key: str, sandbox: Sandbox, cause: str) -> None:
        if split_key(key)[0] in self._m._creating:
            return  # announced once the session's journal exists (create)
        await self._m._announce_hand(key, sandbox, cause)

    async def lost(self, key: str, sandbox: Sandbox, reason: str, logs: bytes | None) -> None:
        session_id, hand = split_key(key)
        ref = None
        if logs:
            try:
                ref = await asyncio.to_thread(self._m._writer.put_blob, logs)
            except OSError:
                logger.warning("saving lost hand %s's logs failed session=%s", sandbox.id, session_id, exc_info=True)
        await self._m._record(
            session_id,
            HandLost(
                hand_id=sandbox.id, provider=sandbox.info.provider, reason=reason, hand=hand,
                logs_ref=ref, logs_bytes=len(logs) if ref and logs else 0,
            ),
            turn_id=None,
            trace_id=self._m._trace_for(session_id),
            event_key=f"hand:{sandbox.id}:lost",
        )
