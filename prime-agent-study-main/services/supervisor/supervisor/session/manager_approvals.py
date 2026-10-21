"""Human approvals for gated tools, and the policy stops (budget, loop guard)."""

from __future__ import annotations

import logging
from typing import Any, Literal

from supervisor.agent.events import (
    ApprovalRequired,
    ApprovalResolved,
    ErrorEvent,
)
from supervisor.db.models import SessionRow
from supervisor.harness.core.enums import ApprovalOnTimeout

logger = logging.getLogger(__name__)


from supervisor.session.manager_base import SessionManagerBase


class ApprovalOps(SessionManagerBase):
    """Human approvals for gated tools, and the policy stops (budget, loop guard)."""

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
                    session_policies[sid] = (
                        "allow"
                        if manifest.approvals.on_timeout is ApprovalOnTimeout.ALLOW
                        else "deny"
                    )
                else:
                    session_policies[sid] = "deny"
            except Exception:
                logger.warning("approval timeout policy unreadable; denying", exc_info=True)
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
                waiting = await db.get(SessionRow, sid)
                if waiting is not None and waiting.status == "awaiting_approval":
                    waiting.status = "active"
                    await db.commit()
        return expired


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
        return bool(prior.status == "timed_out" and is_allow_choice(prior.choice))

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

    async def _end_runaway_turn(self, session_id: str, runtime: Any, *, detail: str) -> None:
        """A loop guard trip: the error is journaled, the turn aborted, the session kept."""
        await self._record(
            session_id,
            ErrorEvent(error_class="LoopGuard", retryable=False, detail=detail),
            turn_id=None,
            trace_id=self._trace_for(session_id, runtime),
        )
        self._guards.reset(session_id)
        try:
            await runtime.abort()
        except Exception:
            logger.exception("abort after loop guard failed session=%s", session_id)

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
            logger.exception("abort failed session=%s", session_id)
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
            logger.exception("cleanup after policy abort failed session=%s", session_id)
        self._run_traces.pop(session_id, None)
