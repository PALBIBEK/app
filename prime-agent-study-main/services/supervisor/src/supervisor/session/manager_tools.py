"""Direct tool calls, and what a session's tools may do and hold."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

from supervisor.agent.events import (
    ApprovalRequired,
    ToolEnd,
    ToolStart,
)
from supervisor.db.models import SessionRow
from supervisor.models.browser import BrowserAccess, BrowserGrant
from supervisor.seams import (
    ModelGatewayClient,
    ToolClassifier,
    ToolInvokeContext,
    ToolProvider,
    ToolResult,
)
from supervisor.session.errors import (
    SessionAwaitingApproval,
    SessionNotFound,
    ToolInvokeNotConfigured,
)
from supervisor.session.manager_approvals import ApprovalOps
from supervisor.session.manager_base import ToolInvokeOutcome
from supervisor.tools import SandboxWorkspace


class ToolOps(ApprovalOps):
    """Direct tool calls, and what a session's tools may do and hold."""

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
                    result={"ok": False, "payload": None, "error": denial, "code": "FILE_EFFECTS_DENIED"},
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
