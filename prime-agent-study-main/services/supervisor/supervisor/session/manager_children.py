"""Subagent sessions as a session sees them, and memory operations."""

from __future__ import annotations

import logging
from typing import Literal

from sqlalchemy import select

from supervisor.db.models import SessionRow
from supervisor.seams import CompactionPreparation
from supervisor.session.errors import MemoryNotConfigured, SessionNotFound
from supervisor.session.manager_base import runs_open
from supervisor.session.manager_tools import ToolOps
from supervisor.session.memory_ops import CompactMemoryOutcome, MemoryOps, RecallMemoryOutcome
from supervisor.session.subagent_view import SubagentView, subagent_views
from supervisor.session.workspaces import session_workspace

logger = logging.getLogger(__name__)

MAX_TREE = 500
"""Subagents of one session read at most (a tree that large is a runaway)."""


class ChildOps(ToolOps):
    """Subagent sessions (``supervisor.session.children``), and memory operations."""

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

    async def subagents(self, session_id: str, only: str | None = None) -> list[SubagentView]:
        """Every subagent below the session, each level after the one above it, or just
        ``only`` when it is one (``supervisor.session.subagent_view``)."""
        ids = [sid for sid, _ in await self.session_tree(session_id)][1:]
        if only is not None:
            ids = [sid for sid in ids if sid == only]
        async with self._db() as db:
            return await subagent_views(db, session_id, ids, await runs_open(db, ids))

    async def refuses_messages(self, session_id: str) -> str | None:
        """Why a person may not message the session, if they may not: a subagent works on
        the task its parent gave it, and only a continuable one takes more (githi's
        ``send_message``), from its parent or from the person."""
        row = await self._get_session_row(session_id)
        if row.parent_session_id is None or row.subagent_kind == "continuable":
            return None
        return (
            "this subagent works on the one task it was given and takes no messages; "
            "message the session that started it instead"
        )

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
