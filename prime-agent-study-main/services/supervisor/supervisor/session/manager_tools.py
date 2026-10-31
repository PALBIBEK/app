"""Direct tool calls, and what a session's tools may do and hold."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, replace
from typing import Any, Final
from uuid import uuid4

from supervisor.agent.events import (
    ApprovalRequired,
    ToolEnd,
    ToolStart,
)
from supervisor.catalog import SessionPins
from supervisor.db.models import SessionRow
from supervisor.hands import HandWorkspace
from supervisor.hands.keys import DEFAULT_HAND, hand_key, split_key, take_hand
from supervisor.hands.leases import Borrower
from supervisor.models.browser import BrowserAccess, BrowserGrant
from supervisor.seams import (
    ModelGatewayClient,
    ToolClassifier,
    ToolInvokeContext,
    ToolProvider,
    ToolResult,
)
from supervisor.seams.interfaces.tool_provider import needs_workspace
from supervisor.session.errors import (
    SessionAwaitingApproval,
    SessionNotFound,
    ToolInvokeNotConfigured,
)
from supervisor.session.manager_approvals import ApprovalOps
from supervisor.session.manager_base import ToolInvokeOutcome
from supervisor.tools import WorkspaceReset
from supervisor.tools.truncate import cut_middle

logger = logging.getLogger(__name__)

MAX_KEPT_RESULT_CHARS: Final = 128 * 1024
"""What a session keeps of one tool result (its journal, a direct call's answer). Well
past what the model reads of it (``render.MAX_RESULT_CHARS``), and far within what one
item of a workflow's history may hold (2 MB): a direct call's answer goes through it."""


def kept(result: ToolResult) -> ToolResult:
    """``result`` within ``MAX_KEPT_RESULT_CHARS``: a larger payload (a tool that does not
    bound its own output, an MCP server's) is kept as its text's head and tail, and so
    is a larger error."""
    payload, error = result.payload, result.error
    if payload is not None:
        text = payload if isinstance(payload, str) else json.dumps(payload, default=str, ensure_ascii=False)
        if len(text) > MAX_KEPT_RESULT_CHARS:
            payload = cut_middle(text, MAX_KEPT_RESULT_CHARS)
    if error is not None:
        error = cut_middle(error, MAX_KEPT_RESULT_CHARS)
    if payload is result.payload and error == result.error:
        return result
    return replace(result, payload=payload, error=error)


@dataclass(frozen=True, slots=True)
class HandForCall:
    """The sandbox serving one tool call, or why there is none."""

    hand_id: str | None = None
    unavailable: ToolResult | None = None
    """Set when the call needs a sandbox and none could be built: the call's result."""
    key: str | None = None
    """Which of the session's hands serves it (``supervisor.hands.keys``)."""


class ToolOps(ApprovalOps):
    """Direct tool calls, and what a session's tools may do and hold."""

    def hand_names(self, session_id: str) -> list[str]:
        """The session's hands by name, its default hand first (one entry: no named hands)."""
        return [split_key(key)[1] for key in self._hands.keys_of(session_id)]

    @staticmethod
    def pinned_hand_names(row: SessionRow) -> list[str]:
        """The hands ``row``'s composition pins, by name, the default first: what a subagent
        is offered, whose calls run in its root's hands (pinned the same way)."""
        pins = SessionPins.from_dict(dict(row.composition_pins)) if row.composition_pins else None
        return [DEFAULT_HAND, *(name for name, _, _ in (pins.hands if pins is not None else ()))]

    async def hand_for_call(
        self,
        session_id: str,
        name: str,
        provider: ToolProvider | None,
        hand: str | None = None,
        borrower: Borrower | None = None,
    ) -> HandForCall:
        """Before a tool runs: a sandbox for it if it needs one, built now if none serves.

        ``hand`` names which of the session's hands (default: its default one).
        A lent hand serves only its holder (``borrower``, fenced by its token);
        the session's own calls on it are refused until it comes back.
        Runs on the supervisor's loop (building and hydrating touch the database);
        the tool itself then runs on its own. A sandbox that cannot be built is the
        model's to read (``WORKSPACE_UNAVAILABLE``), never a lost tool outcome.
        """
        if not needs_workspace(provider, name):
            return HandForCall()
        key = hand_key(session_id, hand)
        if key != session_id and key not in self._hands:
            return HandForCall(
                unavailable=ToolResult(
                    ok=False,
                    error=f"this session has no hand {hand!r}; its hands: "
                    f"{', '.join(self.hand_names(session_id))}",
                    code="UNKNOWN_HAND",
                )
            )
        if (refused := await self._lease_refusal(key, borrower)) is not None:
            return HandForCall(unavailable=refused)
        try:
            sandbox = await self._hands.ensure(key)
        except Exception as exc:
            logger.exception("building a sandbox failed hand=%s", key)
            return HandForCall(
                unavailable=ToolResult(
                    ok=False,
                    error=f"the session's sandbox could not be started: {exc}",
                    code="WORKSPACE_UNAVAILABLE",
                )
            )
        if key == session_id and (ws := self._workspace.get(session_id)) is not None:
            self._workspace_svc.assert_ready(ws)
        if sandbox is not None:
            self._hands.begin_use(key)  # an idle stop never lands under a running tool
        return HandForCall(hand_id=sandbox.id if sandbox is not None else None, key=key)

    async def _lease_refusal(self, key: str, borrower: Borrower | None) -> ToolResult | None:
        """Why a call may not use the hand now (it is lent elsewhere, or the lease ended)."""
        live = await self._leases.current(key)
        if borrower is None:
            if live is None:
                return None
            hand = split_key(key)[1]
            return ToolResult(
                ok=False,
                error=f"your {hand!r} hand is lent to {live.holder}; it comes back when that "
                "job ends. Use another hand, or wait for the job.",
                code="HAND_LENT",
            )
        if live is None or live.holder != borrower.holder or live.token != borrower.token:
            return ToolResult(
                ok=False,
                error="the lease on this hand has ended; the hand is back with its session",
                code="LEASE_ENDED",
            )
        await self._leases.renew(live)  # acting keeps the lease alive
        return None

    def call_ended(self, hand: HandForCall) -> None:
        """A call that got a sandbox ended without ``after_call`` (cached, or it raised)."""
        if hand.hand_id is not None:
            self._hands.end_use(hand.key or "")

    def hand_workspace(self, key: str) -> HandWorkspace:
        """Where a tool acts in one of a session's hands (follows that hand's sandbox)."""
        return HandWorkspace(lambda: self._hands.sandbox(key))

    async def after_call(
        self,
        session_id: str,
        hand: HandForCall,
        name: str,
        provider: ToolProvider | None,
        result: ToolResult,
    ) -> ToolResult:
        """After a tool ran: keep its result within bounds (``kept``); in a sandbox, note
        what it changed, or let a dead sandbox go.

        A reset result is rewritten to say what the model gets back (the last
        checkpoint) and what it must redo (processes, rebuilt folders).
        """
        result = kept(result)
        if hand.hand_id is None:
            return result
        key = hand.key or session_id
        self._hands.end_use(key)
        if result.code == WorkspaceReset.code:
            await self._hands.lose(key, hand.hand_id, result.error or "the sandbox stopped")
            restored = (
                self._checkpoints.notice(key)
                if self._checkpoints is not None
                else "nothing was saved, so it starts empty (or from the session's scope)."
            )
            return replace(result, error=f"{result.error} In it: {restored}")
        pure = isinstance(provider, ToolClassifier) and provider.tool_class(name) == "pure"
        sandbox = self._hands.sandbox(key)
        if self._checkpoints is not None and not pure and sandbox is not None:
            try:
                await self._checkpoints.observe(key, sandbox, processes=self._hand_isolated(key))
            except Exception:
                logger.warning("observing sandbox failed hand=%s", key, exc_info=True)
        return result

    async def tool_env(self, session_id: str) -> dict[str, str]:
        """What this turn's commands may hold: the browse runner's model access, if any.

        A fresh browser capability per turn (an hour long), bound to the session,
        its owner and its model; only for a sandbox profile that asks for it
        (``browser_model``), never for strict sessions or without a model.
        """
        if self._browser is None:
            return {}
        row = await self._get_session_row(session_id)
        profile = self._sandbox_profile_for_row(row)
        model = self.session_model(row)
        if not model or profile is None or not profile.browser_model or self.strict_posture(row):
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

    def tool_context(
        self, session_id: str, tenant_id: str, *, key: str | None = None
    ) -> dict[str, Any]:
        """``ToolInvokeContext.extra`` for a session: identity, its workspace, change hook.

        ``key``: a named hand to act in instead of the default one.
        """
        extra: dict[str, Any] = {"session_id": session_id, "tenant_id": tenant_id}
        if (identity := self._long_term.get(session_id)) is not None:
            extra["owner"], extra["agent"] = identity
        runtime_workspace = getattr(self._registry.get(session_id), "workspace", None)
        if key is not None and key != session_id:
            extra["workspace"] = self.hand_workspace(key)
        elif runtime_workspace is not None:
            extra["workspace"] = runtime_workspace
        elif self._hands.recipe(session_id) is not None:
            extra["workspace"] = self.hand_workspace(session_id)
        if (ws := self._workspace.get(session_id)) is not None:
            extra["on_mutate"] = lambda: self._workspace_svc.mark_dirty(ws)
        return extra

    async def invoke_tool(
        self,
        session_id: str,
        *,
        tenant_id: str,
        name: str,
        arguments: dict[str, Any],
        tool_call_id: str | None = None,
        borrower: Borrower | None = None,
    ) -> ToolInvokeOutcome:
        """Run one tool call in the session (``borrower``: on a hand it lent, by its holder)."""
        row = await self._require_active(session_id)
        if row.tenant_id != tenant_id:
            raise SessionNotFound(f"unknown session_id={session_id!r}")
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
                    result={"ok": False, "payload": None, "error": denial, "code": "FILE_EFFECTS_DENIED"},
                    is_error=True,
                ),
                turn_id=None,
                trace_id=run_trace,
            )
            return ToolInvokeOutcome(tool_call_id=call_id, result=result)

        # Pre-invoke require_approval gate (before outbox / provider body).
        if (
            await self._resolve_require_approval(session_id, name)
            and not await self._harness_request_allowed(session_id, call_id)
        ):
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

        if borrower is not None:
            which: str | None = borrower.hand
        else:
            arguments, which = take_hand(arguments, self.hand_names(session_id))
        hand = await self.hand_for_call(session_id, name, seams.tool_provider, which, borrower)
        ctx = ToolInvokeContext(
            trace_id=call_id, extra=self.tool_context(session_id, tenant_id, key=hand.key)
        )
        begin = await self._tool_outbox.begin(
            session_id,
            tool_call_id=call_id,
            tool_name=name,
            arguments=dict(arguments),
            trace_id=run_trace,
        )
        if begin.action == "cached" and begin.cached is not None:
            self.call_ended(hand)
            return ToolInvokeOutcome(tool_call_id=call_id, result=begin.cached)

        # Tools block (files, sandboxes, HTTP); off the loop so the worker's heartbeats
        # and every other session it holds keep running.
        if hand.unavailable is not None:
            result = hand.unavailable
        else:
            try:
                result = await asyncio.to_thread(seams.tool_provider.invoke, ctx, name, arguments)
            except BaseException:
                self.call_ended(hand)
                raise
            result = await self.after_call(session_id, hand, name, seams.tool_provider, result)
        result = await self._tool_outbox.complete(
            session_id,
            tool_call_id=call_id,
            tool_name=name,
            result=result,
            trace_id=run_trace,
        )
        # No model step follows a direct call to overlap with: save its changes now.
        await self.checkpoint(session_id)
        return ToolInvokeOutcome(tool_call_id=call_id, result=result)
