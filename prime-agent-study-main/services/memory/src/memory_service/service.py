"""MemoryService facade — Tier 1 protocol."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from memory_service.bank import MemoryBank
from memory_service.buffer import WriteBehindBuffer
from memory_service.cost import CachingEmbedder, flush_batch_is_barren
from memory_service.db import init_db, make_engine, make_session_factory
from memory_service.embed import Embedder
from memory_service.erase import EraseService
from memory_service.extract import ExtractionQueue, Extractor
from memory_service.leases import LeaseManager
from memory_service.metrics import METRICS
from memory_service.outbox import Outbox
from memory_service.recall import HybridRecall
from memory_service.recovery import (
    assert_format_supported,
    build_recovery_closers,
    detect_seq_gap,
)
from memory_service.retention import ObjectArchive, RetentionService
from memory_service.store import DurableStore
from memory_service.tenant import require_scope
from memory_service.tier2_types import (
    ForgetSelector,
    MemoryItem,
    NewMemoryItem,
    RecallResult,
    TokenBudget,
)
from memory_service.types import (
    FORMAT_VERSION,
    FormatUnsupported,
    LogicalSession,
    Scope,
    SessionCorrupt,
    SessionEvent,
    SessionHandle,
    SessionNotFound,
    SessionSummary,
    StaleFence,
)


class SeqConflict(Exception):
    """Durable seq does not match expected contiguous next."""


class MemoryService:
    """
    Tier 1 durable session log.

    Hot path: buffer.append (sync). Durability: flush (await).
    """

    def __init__(
        self,
        *,
        database_url: str = "sqlite+aiosqlite:///:memory:",
        engine: AsyncEngine | None = None,
        lease_ttl_ms: int = 30_000,
        embedder: Embedder | None = None,
        extractor: Extractor | None = None,
        archive_dir: str | None = None,
        hot_days: int = 30,
    ) -> None:
        self._engine = engine or make_engine(database_url)
        self._factory: async_sessionmaker[AsyncSession] = make_session_factory(self._engine)
        self._store = DurableStore(self._factory)
        self._leases = LeaseManager(self._factory, ttl_ms=lease_ttl_ms)
        self._outbox = Outbox(self._factory)
        self._embedder = embedder or CachingEmbedder()
        self._bank = MemoryBank(self._factory, self._embedder)
        self._extraction = ExtractionQueue(self._factory, self._bank, extractor)
        self._recall = HybridRecall(self._bank, self._embedder)
        self._erase = EraseService(self._factory)
        arch_root = archive_dir or "memory_archive"
        self._retention = RetentionService(
            self._factory, ObjectArchive(arch_root), hot_days=hot_days
        )
        self._buffers: dict[str, WriteBehindBuffer] = {}
        self._ready = False
        self._last_extracted_seq: dict[str, int] = {}

    async def startup(self) -> None:
        await init_db(self._engine)
        self._ready = True

    async def aclose(self) -> None:
        await self._engine.dispose()

    async def ping_db(self) -> bool:
        """Whether the database answers (the readiness probe)."""
        try:
            async with self._engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception:  # noqa: BLE001 — any failure means not ready
            return False
        return True

    async def session_tenant(self, session_id: str) -> str | None:
        """The tenant a logical session belongs to, or None if there is no such session."""
        try:
            row = await self._store.get_session_row(session_id)
        except SessionNotFound:
            return None
        return str(row.tenant)

    def _buf(self, session_id: str) -> WriteBehindBuffer:
        if session_id not in self._buffers:
            self._buffers[session_id] = WriteBehindBuffer()
        return self._buffers[session_id]

    async def create_session(self, scope: Scope, *, session_id: str | None = None) -> SessionHandle:
        if not self._ready:
            await self.startup()
        handle = await self._store.create_session(scope, session_id=session_id)
        buf = self._buf(handle.id)
        buf.seed_next_seq(0)
        return handle

    def append(
        self,
        session_id: str,
        type: str,
        data: Any = None,
        *,
        time_ms: int | None = None,
    ) -> SessionEvent:
        """Synchronous in-memory append — no I/O."""
        return self._buf(session_id).append(type, data, time_ms=time_ms)

    def pending_count(self, session_id: str) -> int:
        return self._buf(session_id).pending_count

    async def flush(self, session_id: str, *, fence: int | None = None) -> int:
        """Persist buffered events at a semantic checkpoint. Returns last durable seq."""
        if not self._ready:
            await self.startup()
        await self._leases.assert_fence(session_id, fence)
        buf = self._buf(session_id)
        row = await self._store.get_session_row(session_id)
        expected = row.last_seq + 1
        pending = list(buf.peek())
        if pending and pending[0].seq != expected:
            # Rehydrate after process crash lost buffer state but durable tip advanced
            renumbered = [
                SessionEvent(
                    type=ev.type,
                    seq=expected + i,
                    time=ev.time,
                    data=ev.data,
                )
                for i, ev in enumerate(pending)
            ]
            buf.drain()
            for ev in renumbered:
                buf._pending.append(ev)
            buf._next_seq = expected + len(renumbered)
        batch = list(buf.peek())
        if not batch:
            return row.last_seq
        outbox_items: list[dict[str, Any]] = []
        for ev in batch:
            item = Outbox.item_for_tool_call(ev)
            if item is not None:
                outbox_items.append(item)
        last = await self._store.flush_events(
            session_id, batch, outbox_items=outbox_items or None
        )
        buf.drain()
        # Off request path: enqueue extraction when turn/end + non-barren
        try:
            if any(ev.type == "turn/end" for ev in batch):
                if flush_batch_is_barren(batch):
                    METRICS.inc("extraction_jobs_skipped_barren")
                else:
                    prev = self._last_extracted_seq.get(session_id, -1)
                    seq_from = prev + 1
                    await self._extraction.enqueue(session_id, seq_from, last)
                    self._last_extracted_seq[session_id] = last
        except Exception:
            pass
        return last

    async def load(self, session_id: str) -> LogicalSession:
        if not self._ready:
            await self.startup()
        row = await self._store.get_session_row(session_id)
        try:
            assert_format_supported(row.format_version)
        except FormatUnsupported:
            raise
        if row.status == "corrupt":
            METRICS.inc("resume_corrupt")
            raise SessionCorrupt(f"SESSION_CORRUPT: {session_id}", code="SESSION_CORRUPT")
        if row.status == "erased":
            raise SessionNotFound(f"SESSION_NOT_FOUND: erased {session_id}")

        if row.status == "archived":
            events = await self._retention.load_archived_events(session_id)
        else:
            events = await self._store.load_events(session_id)
        gap = detect_seq_gap(events)
        if gap is not None:
            await self._store.set_status(session_id, "corrupt")
            METRICS.inc("resume_corrupt")
            raise SessionCorrupt(
                f"SESSION_CORRUPT: seq gap at expected {gap}",
                code="SESSION_CORRUPT",
            )

        closers = build_recovery_closers(events)
        repaired = False
        if closers and row.status != "archived":
            await self._store.flush_events(session_id, closers)
            events = events + closers
            repaired = True
            await self._store.set_status(session_id, "interrupted")
            row = await self._store.get_session_row(session_id)

        # Seed buffer for continued appends
        buf = self._buf(session_id)
        if buf.pending_count == 0:
            buf.seed_next_seq(len(events))

        handle = SessionHandle(
            id=session_id,
            scope=Scope(tenant=row.tenant, subject=row.subject, namespace=row.namespace),
            format_version=row.format_version,
        )
        METRICS.inc("resume_ok")
        return LogicalSession(
            handle=handle,
            status=row.status,
            events=tuple(events),
            parent_id=row.parent_id,
            forked_at_seq=row.forked_at_seq,
            created_at=row.created_at,
            closed_at=row.closed_at,
            repaired=repaired,
        )

    async def fork(self, session_id: str, at_seq: int | None = None) -> SessionHandle:
        if not self._ready:
            await self.startup()
        # Ensure source is loadable / not corrupt
        await self.load(session_id)
        handle = await self._store.fork(session_id, at_seq=at_seq)
        self._buf(handle.id).seed_next_seq(
            (await self._store.get_session_row(handle.id)).last_seq + 1
        )
        return handle

    async def list_sessions(self, scope: Scope) -> list[SessionSummary]:
        if not self._ready:
            await self.startup()
        return await self._store.list_sessions(scope)

    async def acquire_lease(self, session_id: str, owner: str) -> int:
        if not self._ready:
            await self.startup()
        return await self._leases.acquire(session_id, owner)

    async def execute_outbox(self, session_id: str, executor: Any) -> list[dict[str, Any]]:
        if not self._ready:
            await self.startup()
        return await self._outbox.execute_pending(session_id, executor)

    # --- Tier 2 ---

    async def write(self, scope: Scope, item: NewMemoryItem) -> str:
        if not self._ready:
            await self.startup()
        require_scope(scope)
        return await self._bank.write(scope, item)

    async def supersede(self, item_id: str, replacement: NewMemoryItem, scope: Scope) -> str:
        if not self._ready:
            await self.startup()
        require_scope(scope)
        return await self._bank.supersede(item_id, replacement, scope)

    async def get(self, item_id: str) -> MemoryItem | None:
        if not self._ready:
            await self.startup()
        return await self._bank.get(item_id)

    async def forget(
        self,
        scope: Scope,
        selector: ForgetSelector,
        *,
        actor: str = "system",
        reason: str = "unspecified",
    ) -> int:
        if not self._ready:
            await self.startup()
        return await self._erase.forget(scope, selector, actor=actor, reason=reason)

    async def erase_subject(
        self, *, tenant: str, subject: str, actor: str, reason: str
    ) -> dict[str, int]:
        if not self._ready:
            await self.startup()
        return await self._erase.erase_subject(
            tenant=tenant, subject=subject, actor=actor, reason=reason
        )

    async def archive_session(self, session_id: str) -> dict:
        if not self._ready:
            await self.startup()
        return await self._retention.archive_session(session_id)

    async def metrics_snapshot(self) -> dict:
        return METRICS.snapshot()

    async def report_recall_used(self, n: int = 1) -> None:
        METRICS.inc("recall_items_marked_used", n)

    async def recall(
        self,
        scope: Scope,
        query: str,
        budget: TokenBudget,
        *,
        log_session_id: str | None = None,
    ) -> RecallResult:
        if not self._ready:
            await self.startup()
        require_scope(scope)
        result = await self._recall.recall(scope, query, budget)
        METRICS.inc("recall_injections")
        METRICS.inc("recall_items_injected", len(result.item_ids))
        if log_session_id and result.item_ids:
            self.append(
                log_session_id,
                "memory/recall",
                {
                    "item_ids": list(result.item_ids),
                    "query": query,
                    "tokens_used": result.tokens_used,
                },
            )
            await self.flush(log_session_id)
        elif log_session_id:
            self.append(
                log_session_id,
                "memory/recall",
                {"item_ids": [], "query": query, "tokens_used": 0},
            )
            await self.flush(log_session_id)
        return result

    async def process_extraction_jobs(self, *, limit: int = 50) -> int:
        if not self._ready:
            await self.startup()
        return await self._extraction.process_pending(limit=limit)

    async def rebuild_tier2_from_tier1(self, scope: Scope, session_id: str) -> int:
        """Wipe active bank for scope and re-extract from full session log."""
        if not self._ready:
            await self.startup()
        await self._bank.wipe_scope(scope)
        row = await self._store.get_session_row(session_id)
        if row.last_seq < 0:
            return 0
        await self._extraction.enqueue(session_id, 0, row.last_seq)
        return await self._extraction.process_pending()

    async def extraction_pending_count(self) -> int:
        if not self._ready:
            await self.startup()
        return await self._extraction.pending_count()

    async def inject_corrupt_gap_for_test(self, session_id: str) -> None:
        """Test-only: insert event at wrong seq to simulate corruption."""
        import time

        from memory_service.models import MemorySessionRow, SessionEventRow

        async with self._factory() as db:
            db.add(
                SessionEventRow(
                    session_id=session_id,
                    seq=99,
                    type="user/message",
                    time=int(time.time() * 1000),
                    data={"text": "gap"},
                )
            )
            row = await db.get(MemorySessionRow, session_id)
            if row:
                row.last_seq = max(row.last_seq, 99)
            await db.commit()

    def assert_assistant_before_tools(self, session_id: str) -> None:
        """
        Ordering guard for adapters: among *pending* events, every tool/call
        must be preceded (earlier seq) by an assistant/message in the same flush batch
        or already durable. Raises AssertionError if violated in pending buffer.
        """
        pending = self._buf(session_id).peek()
        last_assistant_seq = -1
        for ev in pending:
            if ev.type == "assistant/message":
                last_assistant_seq = ev.seq
            if ev.type == "tool/call" and last_assistant_seq < 0:
                # allow if durable already has assistant — check is best-effort on buffer
                raise AssertionError(
                    "assistant/message must be appended before tool/call in the same turn buffer"
                )


# Re-export errors for callers
__all__ = [
    "FORMAT_VERSION",
    "FormatUnsupported",
    "MemoryService",
    "SeqConflict",
    "SessionCorrupt",
    "SessionNotFound",
    "StaleFence",
]
