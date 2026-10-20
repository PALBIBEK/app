"""Session lifecycle — decisions live here; facts live in the journal."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from supervisor.agent.events import (
    ApprovalRequired,
    ErrorEvent,
    MessageEnd,
    RunEnd,
    RunStart,
    ToolStart,
    TurnStart,
)
from supervisor.agent.factory import create_runtime
from supervisor.agent.seams import RuntimeSeams
from supervisor.catalog import HarnessVersionMismatch, SessionPins
from supervisor.catalog.providers.binder import bind_runtime_seams
from supervisor.catalog.providers.memory_resolve import load_pinned_memory_profile
from supervisor.catalog.repository import sync_composition
from supervisor.catalog.services.resolver import require_tenant_access, resolve_composition
from supervisor.db.models import ScopeRow, SessionIndexRow, SessionRow
from supervisor.hands import HandRecipe, HandWorkspace
from supervisor.harness import (
    HarnessManifest,
    manifest_to_dict,
    materialize_harness_binds,
)
from supervisor.harness.core.errors import UnknownHarness
from supervisor.harness.repository import harness_owner, sync_harness_version
from supervisor.harness.repository import visible_to as harness_visible_to
from supervisor.harness.services.compat_checker import capabilities_for, check
from supervisor.memory.binding import MemoryRequest, agent_pin, long_term_agent, resolve_binding
from supervisor.models import model_router
from supervisor.policy.budget import BudgetExceeded
from supervisor.policy.guards import GuardAction, call_hash
from supervisor.sandbox.resolve import load_pinned_sandbox_profile, resolve_sandbox_create
from supervisor.seams import (
    build_runtime_seams,
)
from supervisor.seams.services import (
    Principal,
    SessionWorkspaceState,
    resolve_memory_scope,
)
from supervisor.session.access import session_visibility, visible_to
from supervisor.session.errors import (
    RecoveryBlocked,
)
from supervisor.session.modes import SessionMode, mode_for_manifest
from supervisor.session.recovery import ResumeState
from supervisor.session.workspaces import session_workspace

logger = logging.getLogger(__name__)


# Re-exported: callers import these from here.
from supervisor.session.errors import (  # noqa: F401
    MemoryNotConfigured,
    SessionAwaitingApproval,
    SessionError,
    SessionNotFound,
    SessionTerminated,
    SubagentNotConfigured,
    ToolInvokeNotConfigured,
    WorkspaceUnavailable,
)
from supervisor.session.manager_base import (  # noqa: F401
    ResumeResult,
    SessionInfo,
    ToolInvokeOutcome,
    _summarizer_model,
    session_model,
)
from supervisor.session.manager_upkeep import UpkeepOps


class SessionManager(UpkeepOps):
    """Session lifecycle: create, resume, fork, terminate, and every turn's event fan-out."""

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
            sandbox_ids = list(self._hands.live)
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

        # The sandbox's recipe is resolved now (a config that can never get one is
        # refused here); the sandbox itself is built when a tool first needs it.
        self._hands.register(
            session_id,
            self._hand_recipe(
                manifest,
                session_id=session_id,
                harness_version=harness_version,
                sandbox_profile=profile,
            ),
        )
        self._workspace[session_id] = ws_state
        self._creating.add(session_id)
        try:
            if (recipe := self._hands.recipe(session_id)) is not None:
                await recipe.preflight()  # refuse now what could never get a sandbox
            runtime = await self._start_runtime(
                session_id,
                runtime_name=runtime_name,
                manifest=manifest,
                pins=pins,
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
        except BaseException:
            # Nothing of a half-made session stays: no sandbox, no hand.
            if (half := self._hands.release(session_id)) is not None:
                with contextlib.suppress(Exception):
                    await half.destroy()
            self._workspace.pop(session_id, None)
            raise
        finally:
            self._creating.discard(session_id)

        if (eager := self._hands.sandbox(session_id)) is not None:
            await self._announce_hand(session_id, eager, "eager")
        self._registry.put(session_id, runtime)
        self._modes[session_id] = mode_for_manifest(manifest)
        self._configure_policy(session_id, manifest, sandbox_profile=profile)
        self._start_fanout(session_id, runtime)
        return session_id

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
        # A runtime that takes a workspace gets its sandbox on first use (and a fresh
        # one after a loss); any other runtime needs its sandbox before it starts.
        set_workspace = getattr(runtime, "set_workspace", None)
        if self._hands.recipe(session_id) is not None and callable(set_workspace):
            await runtime.start(None, session_dir)
            set_workspace(HandWorkspace(lambda: self._hands.sandbox(session_id)))
        else:
            await runtime.start(await self._hands.ensure(session_id, cause="eager"), session_dir)
        return runtime

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
                    logger.debug("runtime state unavailable for session info", exc_info=True)
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
        # Only a sandbox that exists can be half-loaded; one built later is checked
        # by the tool call that builds it (hand_for_call).
        if ws is not None and self._hands.sandbox(session_id) is not None:
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
        if session_id not in self._hands:
            self._hands.register(
                session_id,
                self._hand_recipe(
                    harness,
                    session_id=session_id,
                    harness_version=row.harness_version,
                    sandbox_profile=profile,
                ),
            )
        self._workspace[session_id] = ws_state
        # The sandbox a dead worker left, if it survived, keeps serving (F21);
        # otherwise the next tool call that needs one builds it.
        await self._hands.adopt(session_id)
        # V-6: re-bind from stored pins; do not re-resolve floating catalog.
        runtime = await self._start_runtime(
            session_id,
            runtime_name=row.runtime,
            manifest=harness,
            pins=pins,
            sandbox_profile=profile,
            memory_profile=memory_profile,
            tenant_id=row.tenant_id,
            principal=principal,
            ws_state=ws_state,
        )
        history = await self._reader.latest_history(session_id)
        # D13: reconstruct open-span state from journal after cold resume / restart.
        try:
            from supervisor.obs.tracing import get_projector

            proj = get_projector()
            if proj is not None:
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
            logger.exception("span reconstruct on resume failed session=%s", session_id)
        _ = history
        self._registry.put(session_id, runtime)
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

    def _hand_recipe(
        self,
        manifest: HarnessManifest,
        *,
        session_id: str,
        harness_version: str | None = None,
        sandbox_profile: Any | None = None,
    ) -> HandRecipe | None:
        """How to build the session's sandboxes, or None if its harness runs none.

        Resolving checks the harness, the profile and multi-tenant policy now, so a
        session that could never get a sandbox is refused at creation.
        """
        provider, digest = resolve_sandbox_create(
            manifest,
            deploy_provider=self._provider,
            profile=sandbox_profile,
            hydrator=self._workspace_svc.hydrator,
        )
        if provider is None:
            return None
        labels = {
            "supervisor.session_id": session_id,
            "supervisor.harness_version": harness_version or manifest.harness_version,
        }
        if sandbox_profile is not None:
            labels["supervisor.sandbox_profile"] = sandbox_profile.name
        return HandRecipe(provider=provider, image=digest, labels=labels)

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
                    # End the runaway turn and keep pumping, so its end is journaled;
                    # the session stays open for the next message (F32).
                    await self._end_runaway_turn(
                        session_id, runtime, detail=verdict.detail or "loop_detected"
                    )
                    continue

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
                            detail=bstate.exceeded_reason or "budget exceeded",
                            status="budget_exceeded",
                        )
                        return

                if isinstance(payload, RunEnd) and mode == SessionMode.STATELESS_PER_TURN:
                    await self._stop_runtime_keep_session(session_id)
                    return
        except Exception as exc:
            logger.exception("fan-out failed session=%s", session_id)
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
                logger.exception("failed to write terminal event session=%s", session_id)
            self._registry.remove(session_id)
