"""MemoryService facade — Tier 1 protocol."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from supervisor.memory_service.bank import MemoryBank
from supervisor.memory_service.buffer import WriteBehindBuffer
from supervisor.memory_service.cost import CachingEmbedder, flush_batch_is_barren
from supervisor.memory_service.db import init_db, make_engine, make_session_factory
from supervisor.memory_service.embed import Embedder
from supervisor.memory_service.erase import EraseService
from supervisor.memory_service.extract import ExtractionQueue, Extractor
from supervisor.memory_service.leases import LeaseManager
from supervisor.memory_service.limits import BUFFER_IDLE_MS
from supervisor.memory_service.metrics import METRICS
from supervisor.memory_service.notebooks.ops import NotebookOps
from supervisor.memory_service.notebooks.store import NotebookStore
from supervisor.memory_service.outbox import Outbox
from supervisor.memory_service.recall import HybridRecall
from supervisor.memory_service.recovery import (
    assert_format_supported,
    build_recovery_closers,
    detect_seq_gap,
)
from supervisor.memory_service.retention import ObjectArchive, RetentionService
from supervisor.memory_service.store import DurableStore
from supervisor.memory_service.tenant import require_scope
from supervisor.memory_service.tier2_types import (
    ForgetSelector,
    MemoryItem,
    NewMemoryItem,
    RecallResult,
    TokenBudget,
)
from supervisor.memory_service.types import (
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

logger = logging.getLogger(__name__)

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
        if extractor is None:
            from supervisor.memory_service.model_extractor import ModelExtractor

            extractor = ModelExtractor.from_env()
        self._extractor = extractor
        self._extraction = ExtractionQueue(self._factory, self._bank, extractor)
        self._recall = HybridRecall(self._bank, self._embedder)
        self._erase = EraseService(self._factory)
        # The one long-term store: notebooks of facts, indexed into the same bank.
        self.notebooks = NotebookOps(NotebookStore(self._factory, self._embedder))
        arch_root = archive_dir or "memory_archive"
        self._retention = RetentionService(
            self._factory, ObjectArchive(arch_root), hot_days=hot_days
        )
        self._buffers: dict[str, WriteBehindBuffer] = {}
        self._flush_locks: dict[str, asyncio.Lock] = {}
        self._ready = False
        self._background: dict[str, asyncio.Task[None]] = {}
        """Named background loops (extraction, buffer sweep, archiving), stopped by ``aclose``."""

    async def startup(self) -> None:
        await init_db(self._engine)
        self._ready = True

    async def aclose(self) -> None:
        await self.stop_background()
        if (close := getattr(self._extractor, "aclose", None)) is not None:
            await close()
        await self._engine.dispose()

    @property
    def background(self) -> tuple[str, ...]:
        """The background loops running now."""
        return tuple(name for name, task in self._background.items() if not task.done())

    def _start(self, name: str, loop: Callable[[], Coroutine[Any, Any, None]]) -> None:
        task = self._background.get(name)
        if task is None or task.done():
            self._background[name] = asyncio.create_task(loop(), name=f"memory-{name}")

    async def stop_background(self) -> None:
        tasks, self._background = list(self._background.values()), {}
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _every(self, interval_s: float, what: str, step: Callable[[], Awaitable[object]]) -> None:
        while True:
            await asyncio.sleep(interval_s)
            try:
                await step()
            except Exception:
                logger.warning("%s failed", what, exc_info=True)

    def start_buffer_sweeper(self, *, interval_s: float = 60.0, idle_ms: int = BUFFER_IDLE_MS) -> None:
        """Run ``sweep_buffers`` every ``interval_s`` (until ``aclose``)."""
        self._start(
            "buffer-sweep", lambda: self._every(interval_s, "buffer sweep", lambda: self.sweep_buffers(idle_ms=idle_ms))
        )

    def start_archiver(self, *, interval_s: float = 3600.0, limit: int = 100) -> None:
        """Run ``archive_cold_sessions`` every ``interval_s`` (until ``aclose``). Replicas may
        each run one: archiving a session locks its row, and archiving it twice is a no-op."""
        self._start(
            "archive", lambda: self._every(interval_s, "archiving", lambda: self.archive_cold_sessions(limit=limit))
        )

    async def sweep_buffers(self, *, idle_ms: int = BUFFER_IDLE_MS) -> dict[str, int]:
        """Bound what this replica holds in memory for sessions nobody is writing to.

        A buffer untouched for ``idle_ms``: its events are flushed (a checkpoint the
        writer never reached is still better than losing them), then the buffer is
        dropped; a buffer whose session does not exist (or was erased) is dropped with
        its events. A session leased to a live writer is left to that writer."""
        cutoff = int(time.time() * 1000) - idle_ms
        out = {"flushed": 0, "dropped": 0}
        for session_id, buf in list(self._buffers.items()):
            lock = self._flush_locks.get(session_id)
            if buf.touched_ms > cutoff or (lock is not None and lock.locked()):
                continue
            if buf.pending_count:
                try:
                    await self.flush(session_id)
                    out["flushed"] += 1
                except SessionNotFound:
                    pass  # nothing to keep them in
                except StaleFence:
                    continue  # its lease holder flushes it
                except Exception:
                    logger.warning("sweep could not flush session=%s", session_id, exc_info=True)
                    continue
            if self._buffers.get(session_id) is buf and (buf.pending_count == 0 or not await self._exists(session_id)):
                del self._buffers[session_id]
                self._flush_locks.pop(session_id, None)
                out["dropped"] += 1
        return out

    async def _exists(self, session_id: str) -> bool:
        try:
            row = await self._store.get_session_row(session_id)
        except SessionNotFound:
            return False
        return row.status != "erased"

    def start_extraction_worker(self, *, interval_s: float = 5.0, limit: int = 50) -> None:
        """Process extraction jobs in the background, every ``interval_s`` (until ``aclose``).

        Replicas may each run one: a job is claimed before it runs."""
        self._start("extraction", lambda: self._extraction_loop(interval_s, limit))

    async def _extraction_loop(self, interval_s: float, limit: int) -> None:
        while True:
            try:
                ran = await self.process_extraction_jobs(limit=limit)
            except Exception:
                logger.warning("extraction worker pass failed", exc_info=True)
                ran = 0
            if ran < limit:  # a full batch means more may be waiting: go again at once
                await asyncio.sleep(interval_s)

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

    def buffer_room(self, session_id: str) -> int:
        """How many more events the session's buffer takes before a flush."""
        return self._buf(session_id).room()

    async def flush(self, session_id: str, *, fence: int | None = None) -> int:
        """Persist buffered events at a semantic checkpoint. Returns last durable seq.

        One flush of a session at a time in this process: a second waits, then finds
        what the first left (nothing, or what was appended meanwhile), instead of
        writing the same batch again and failing on its seq."""
        if not self._ready:
            await self.startup()
        async with self._flush_locks.setdefault(session_id, asyncio.Lock()):
            return await self._flush(session_id, fence)

    async def _flush(self, session_id: str, fence: int | None) -> int:
        await self._leases.assert_fence(session_id, fence)
        buf = self._buf(session_id)
        row = await self._store.get_session_row(session_id)
        if row.status == "erased":  # what was buffered for it goes with it
            self._buffers.pop(session_id, None)
            raise SessionNotFound(f"SESSION_NOT_FOUND: erased {session_id}")
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
            session_id, batch, outbox_items=outbox_items or None, fence=fence, fenced=True
        )
        buf.discard(len(batch))  # what was appended while this ran stays for the next flush
        # Off request path: enqueue extraction when turn/end + non-barren
        try:
            if self._extraction.enabled and any(ev.type == "turn/end" for ev in batch):
                if flush_batch_is_barren(batch):
                    METRICS.inc("extraction_jobs_skipped_barren")
                else:
                    await self._extraction.enqueue_through(session_id, last)
        except Exception:  # extraction is off the request path; the flush itself succeeded
            logger.warning("enqueueing extraction failed session=%s", session_id, exc_info=True)
        return last

    async def load(self, session_id: str) -> LogicalSession:
        if not self._ready:
            await self.startup()
        row = await self._store.get_session_row(session_id)
        assert_format_supported(row.format_version)
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
        # A user's notebooks go first, history included (erasure is not clearing): their
        # facts' bank items are the subject's too, and the notebooks link to them.
        notebooks = None
        if subject.startswith("user:"):
            notebooks = await self.notebooks.store.erase_owner(
                tenant_id=tenant, owner=subject.removeprefix("user:")
            )
        erased = await self._erase.erase_subject(
            tenant=tenant, subject=subject, actor=actor, reason=reason
        )
        if notebooks is not None:
            erased["notebooks_erased"] = notebooks
        return erased

    async def archive_session(self, session_id: str) -> dict:
        if not self._ready:
            await self.startup()
        return await self._retention.archive_session(session_id)

    async def archive_cold_sessions(self, *, limit: int = 100) -> int:
        """Archive up to ``limit`` sessions untouched for ``hot_days`` (``RetentionService``)."""
        if not self._ready:
            await self.startup()
        return await self._retention.archive_cold(limit=limit)

    async def metrics_snapshot(self) -> dict:
        return {**METRICS.snapshot(), **await self._extraction.backlog()}

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

    async def retry_failed_extraction_jobs(self) -> int:
        """Queue the jobs that used up their runs again (after fixing what failed them)."""
        if not self._ready:
            await self.startup()
        return await self._extraction.retry_failed()



    async def inject_corrupt_gap_for_test(self, session_id: str) -> None:
        """Test-only: insert event at wrong seq to simulate corruption."""
        import time

        from supervisor.memory_service.models import MemorySessionRow, SessionEventRow

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
