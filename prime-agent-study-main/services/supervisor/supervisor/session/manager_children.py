"""Child sessions, background subagents, and memory operations."""

from __future__ import annotations

import logging
from typing import Literal

from sqlalchemy import select

from supervisor.db.models import EventRow, SessionRow
from supervisor.seams import (
    CompactionPreparation,
    JobHandle,
    SubagentRequest,
)
from supervisor.session.errors import (
    MemoryNotConfigured,
    SessionNotFound,
    SubagentNotConfigured,
)
from supervisor.session.manager_tools import ToolOps
from supervisor.session.memory_ops import CompactMemoryOutcome, MemoryOps, RecallMemoryOutcome
from supervisor.session.subagent_ops import SubagentOps
from supervisor.session.tree import ChildRow
from supervisor.session.workspaces import session_workspace

logger = logging.getLogger(__name__)

MAX_TREE = 500
"""Subagents of one session read at most (a tree that large is a runaway)."""


class ChildOps(ToolOps):
    """Child sessions, background subagents, and memory operations."""

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
            logger.warning("subagent depth from the journal failed; using the claimed depth", exc_info=True)
            derived = 0
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

    async def session_tree(self, session_id: str) -> list[tuple[str, str | None]]:
        """The session and every subagent below it (``supervisor.session.children``), the
        session first, then each level in turn, with the label each subagent's task got."""
        tree: list[tuple[str, str | None]] = [(session_id, None)]
        level = [session_id]
        async with self._db() as db:
            while level and len(tree) <= MAX_TREE:
                rows = (
                    await db.execute(
                        select(SessionRow.id, SessionRow.subagent_label)
                        .where(SessionRow.parent_session_id.in_(level))
                        .order_by(SessionRow.created_at)
                    )
                ).all()
                tree += [(sid, label) for sid, label in rows]
                level = [sid for sid, _ in rows]
        return tree[: MAX_TREE + 1]

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
            charge=lambda cost: self.charge_cost(session_id, cost),
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
