"""Run traces kept as rollups: one ``run_traces`` row per run, rebuilt from the journal.

The journal is the source of truth; a row is a cache of ``build_run_trace``'s rollup that
lists and filters read without scanning events. Rows are written when a run starts and
when it ends (``record_run``, from the one path every event takes) and refreshed on read
while a run is unfinished, so a crash between the two never leaves a row stale for good.
Runs journalled before this table existed are filled in on a tenant's first listing.
"""

from __future__ import annotations

import logging
import time
import weakref
from dataclasses import dataclass
from typing import Any

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.db.models import EventRow, RunTraceRow, SessionRow
from supervisor.journal.reader import JournalReader
from supervisor.session.access import session_visibility
from supervisor.traces.build import build_run_trace, subagent_runs, trace_id
from supervisor.traces.model import RunStatus, RunTrace
from supervisor.traces.scores import write_checks
from supervisor.traces.summary import describe

__all__ = [
    "TraceFilter",
    "agent_label",
    "list_runs",
    "load_run",
    "record_run",
    "run_events",
    "session_runs",
    "summary",
]

logger = logging.getLogger(__name__)

Factory = async_sessionmaker[AsyncSession]

MAX_NESTING = 8
"""Levels of subagents nested in a run's trace at most (harness depth caps are smaller)."""
BACKFILL_LIMIT = 500
"""Runs filled in per tenant listing, oldest unrecorded first; the rest on later listings."""
_FINISHED = frozenset(s.value for s in RunStatus if s.finished)
ROLLUP_VERSION = 6
"""Bumped when the builder changes what a rollup says; rows from an older one are rebuilt on read."""
_backfilled: weakref.WeakKeyDictionary[Factory, set[str]] = weakref.WeakKeyDictionary()
"""Per database, the tenants whose older runs are all filled in (new runs are recorded as
they happen, so a tenant is checked once per process)."""


def agent_label(row: SessionRow) -> str:
    """The name a trace's root span carries: the pinned composition, else the harness version."""
    pins = row.composition_pins if isinstance(row.composition_pins, dict) else {}
    return str(pins.get("composition_id") or row.harness_version)


def _running(row: SessionRow) -> bool:
    """Whether the session could still be working on an unfinished run."""
    return row.status == "active"


async def load_run(factory: Factory, reader: JournalReader, row: SessionRow, run_id: str) -> RunTrace | None:
    """Run ``run_id``'s trace, built now from the journal (and its rollup row kept in step),
    with the runs of the subagents it started nested in it."""
    trace = await _build(factory, reader, row, run_id, depth=0)
    if trace is not None:
        await _save(factory, row, trace)
    return trace


async def _build(factory: Factory, reader: JournalReader, row: SessionRow, run_id: str, *, depth: int) -> RunTrace | None:
    events = await reader.read_run_window(row.id, run_id)
    nested: dict[str, RunTrace] = {}
    started = subagent_runs(run_id, events) if depth < MAX_NESTING else []
    if started:
        async with factory() as db:
            children = {
                s.id: s
                for s in (await db.execute(select(SessionRow).where(SessionRow.id.in_({c for c, _ in started})))).scalars()
            }
        for child_id, child_run in started:
            if (child := children.get(child_id)) is not None and (
                sub := await _build(factory, reader, child, child_run, depth=depth + 1)
            ) is not None:
                nested[child_run] = sub
    return build_run_trace(
        row.id, run_id, events, agent=agent_label(row), session_running=_running(row), subagents=nested
    )


async def record_run(factory: Factory, reader: JournalReader, session_id: str, run_id: str) -> None:
    """Refresh run ``run_id``'s rollup row; never raises (it runs beside the journal write)."""
    try:
        async with factory() as db:
            row = await db.get(SessionRow, session_id)
        if row is not None:
            await load_run(factory, reader, row, run_id)
    except Exception:
        logger.exception("trace rollup failed session=%s run=%s", session_id, run_id)


async def _save(factory: Factory, session: SessionRow, trace: RunTrace) -> None:
    r = trace.rollup
    pins = session.composition_pins if isinstance(session.composition_pins, dict) else {}
    values: dict[str, Any] = {
        "tenant_id": session.tenant_id,
        "harness_version": session.harness_version,
        "composition_id": pins.get("composition_id"),
        "composition_digest": pins.get("composition_digest"),
        "status": r.status.value,
        "reason": r.reason,
        "started_ms": r.started_ms,
        "ended_ms": r.ended_ms,
        "duration_ms": r.duration_ms,
        "cost": r.cost,
        "input_tokens": r.input_tokens,
        "output_tokens": r.output_tokens,
        "steps": r.steps,
        "tool_calls": r.tool_calls,
        "tool_errors": r.tool_errors,
        "denials": r.denials,
        "errors": r.errors,
        "input_preview": r.input,
        "output_preview": r.output,
        "summary": describe(trace),
        "summary_by": "rules",
        "rollup": {**r.to_dict(), "v": ROLLUP_VERSION},
        "updated_ms": int(time.time() * 1000),
    }
    for _ in range(2):  # a concurrent first write of the same run: the loser updates instead
        try:
            async with factory() as db, db.begin():
                existing = await db.get(RunTraceRow, (trace.session_id, trace.run_id))
                judged = existing is not None and existing.status in _FINISHED and _current(existing)
                if existing is not None and existing.summary_by == "model" and existing.status == r.status.value:
                    values["summary"], values["summary_by"] = existing.summary, "model"  # keep the model's line
                if r.status.finished and not judged:  # it just finished, or an older builder judged it
                    values["checks_failed"] = await write_checks(db, session, trace)
                if existing is None:
                    db.add(RunTraceRow(session_id=trace.session_id, run_id=trace.run_id, **values))
                else:
                    for key, value in values.items():
                        setattr(existing, key, value)
            return
        except IntegrityError:
            continue


def _current(row: RunTraceRow) -> bool:
    """Whether the row was written by this builder (so its rollup and checks are current)."""
    return (row.rollup or {}).get("v") == ROLLUP_VERSION


def summary(row: RunTraceRow) -> dict[str, Any]:
    """A run as a list shows it: status, timing, cost, counts and the first and last words."""
    rollup = row.rollup or {}
    return {
        "session_id": row.session_id,
        "run_id": row.run_id,
        "trace_id": trace_id(row.session_id, row.run_id),
        "status": row.status,
        "reason": row.reason,
        "started_ms": row.started_ms,
        "ended_ms": row.ended_ms,
        "duration_ms": row.duration_ms,
        "harness_version": row.harness_version,
        "composition_id": row.composition_id,
        "composition_digest": row.composition_digest,
        "input": row.input_preview,
        "output": row.output_preview,
        "summary": row.summary,
        "cost": row.cost,
        "input_tokens": row.input_tokens,
        "output_tokens": row.output_tokens,
        "steps": row.steps,
        "tool_calls": row.tool_calls,
        "tool_errors": row.tool_errors,
        "denials": row.denials,
        "errors": row.errors,
        "subagents": rollup.get("subagents", 0),
        "checks_failed": row.checks_failed,
        "feedback_good": row.feedback_good,
        "feedback_bad": row.feedback_bad,
        "time": rollup.get("time", {}),
        "tools": rollup.get("tools", {}),
        "models": rollup.get("models", []),
    }


async def _refresh(factory: Factory, reader: JournalReader, rows: list[RunTraceRow]) -> list[RunTraceRow]:
    """``rows`` with every unfinished run, and every row an older builder made, rebuilt."""
    stale = [r for r in rows if r.status not in _FINISHED or not _current(r)]
    if not stale:
        return rows
    async with factory() as db:
        sessions = {
            s.id: s
            for s in (
                await db.execute(select(SessionRow).where(SessionRow.id.in_({r.session_id for r in stale})))
            ).scalars()
        }
    for r in stale:
        if (session := sessions.get(r.session_id)) is not None:
            await load_run(factory, reader, session, r.run_id)
    async with factory() as db:
        fresh = {
            (t.session_id, t.run_id): t
            for t in (
                await db.execute(
                    select(RunTraceRow).where(
                        or_(*(and_(RunTraceRow.session_id == r.session_id, RunTraceRow.run_id == r.run_id)
                              for r in stale))
                    )
                )
            ).scalars()
        }
    return [fresh.get((r.session_id, r.run_id), r) for r in rows]


async def _unrecorded(factory: Factory, where: Any, limit: int) -> list[tuple[str, str]]:
    """Runs (session, run) with a RunStart in the journal and no rollup row, oldest first."""
    async with factory() as db:
        found = (
            await db.execute(
                select(EventRow.session_id, EventRow.turn_id)
                .join(SessionRow, SessionRow.id == EventRow.session_id)
                .where(where, EventRow.type == "RunStart", EventRow.turn_id.is_not(None))
                .where(
                    ~exists().where(
                        RunTraceRow.session_id == EventRow.session_id, RunTraceRow.run_id == EventRow.turn_id
                    )
                )
                .order_by(EventRow.ts_ms.asc())
                .limit(limit)
            )
        ).all()
    return [(sid, str(tid)) for sid, tid in found]


async def _fill(factory: Factory, reader: JournalReader, runs: list[tuple[str, str]]) -> None:
    if not runs:
        return
    async with factory() as db:
        sessions = {
            s.id: s
            for s in (await db.execute(select(SessionRow).where(SessionRow.id.in_({sid for sid, _ in runs})))).scalars()
        }
    for sid, run_id in runs:
        if (session := sessions.get(sid)) is not None:
            await load_run(factory, reader, session, run_id)


async def session_runs(factory: Factory, reader: JournalReader, row: SessionRow) -> list[dict[str, Any]]:
    """Every run of the session, oldest first, each as ``summary`` shows it."""
    await _fill(factory, reader, await _unrecorded(factory, SessionRow.id == row.id, BACKFILL_LIMIT))
    async with factory() as db:
        rows = list(
            (
                await db.execute(
                    select(RunTraceRow).where(RunTraceRow.session_id == row.id).order_by(RunTraceRow.started_ms.asc())
                )
            ).scalars()
        )
    return [summary(r) for r in await _refresh(factory, reader, rows)]


@dataclass(frozen=True)
class TraceFilter:
    statuses: tuple[str, ...] = ()
    session_id: str | None = None
    composition_id: str | None = None
    composition_digest: str | None = None
    with_errors: bool = False
    """Only runs where something failed (an error, or a tool that errored)."""
    with_denials: bool = False
    failing_checks: bool = False
    """Only runs that failed an automatic check."""
    feedback: str | None = None
    """``good`` or ``bad``: only runs someone judged so."""
    text: str | None = None
    """Words in the run's request or answer."""
    since_ms: int | None = None
    before_ms: int | None = None
    """Cursor: runs that started before this (the last row of the previous page)."""


async def list_runs(
    factory: Factory,
    reader: JournalReader,
    *,
    tenant_id: str,
    principal_id: str | None,
    filters: TraceFilter,
    limit: int = 50,
) -> tuple[list[dict[str, Any]], int | None]:
    """The caller's runs, newest first, and the cursor for the next page (None at the end)."""
    # A subagent's runs are in the trace of the run that started it, not listed on their own.
    visible = and_(
        SessionRow.tenant_id == tenant_id,
        session_visibility(tenant_id, principal_id),
        SessionRow.parent_session_id.is_(None),
    )
    done = _backfilled.setdefault(factory, set())
    if tenant_id not in done:
        missing = await _unrecorded(factory, SessionRow.tenant_id == tenant_id, BACKFILL_LIMIT)
        await _fill(factory, reader, missing)
        if len(missing) < BACKFILL_LIMIT:
            done.add(tenant_id)
    query = (
        select(RunTraceRow)
        .join(SessionRow, SessionRow.id == RunTraceRow.session_id)
        .where(visible, RunTraceRow.tenant_id == tenant_id)
    )
    f = filters
    if f.statuses:
        query = query.where(RunTraceRow.status.in_(f.statuses))
    if f.session_id:
        query = query.where(RunTraceRow.session_id == f.session_id)
    if f.composition_id:
        query = query.where(RunTraceRow.composition_id == f.composition_id)
    if f.composition_digest:
        query = query.where(RunTraceRow.composition_digest == f.composition_digest)
    if f.with_errors:
        query = query.where(or_(RunTraceRow.errors > 0, RunTraceRow.tool_errors > 0))
    if f.with_denials:
        query = query.where(RunTraceRow.denials > 0)
    if f.failing_checks:
        query = query.where(RunTraceRow.checks_failed > 0)
    if f.feedback == "good":
        query = query.where(RunTraceRow.feedback_good > 0)
    elif f.feedback == "bad":
        query = query.where(RunTraceRow.feedback_bad > 0)
    if f.text:
        like = f"%{f.text.lower()}%"
        query = query.where(
            or_(RunTraceRow.input_preview.ilike(like), RunTraceRow.output_preview.ilike(like))
        )
    if f.since_ms is not None:
        query = query.where(RunTraceRow.started_ms >= f.since_ms)
    if f.before_ms is not None:
        query = query.where(RunTraceRow.started_ms < f.before_ms)
    limit = max(1, min(limit, 200))
    async with factory() as db:
        rows = list((await db.execute(query.order_by(RunTraceRow.started_ms.desc()).limit(limit + 1))).scalars())
    more = len(rows) > limit
    rows = await _refresh(factory, reader, rows[:limit])
    return [summary(r) for r in rows], (rows[-1].started_ms if more and rows else None)


async def run_events(reader: JournalReader, session_id: str, run_id: str) -> list[dict[str, Any]]:
    """The events run ``run_id``'s trace is built from, as recorded (none: no such run)."""
    return [e.to_dict() for e in await reader.read_run_window(session_id, run_id)]


async def set_model_summary(factory: Factory, session_id: str, run_id: str, text: str) -> None:
    """Keep a model's one-line summary of the run (it outlives rebuilds of the rollup)."""
    async with factory() as db, db.begin():
        row = await db.get(RunTraceRow, (session_id, run_id))
        if row is not None:
            row.summary, row.summary_by = text, "model"


async def stored_summary(factory: Factory, session_id: str, run_id: str) -> str | None:
    """The run's one-line summary as kept (a model's, when one wrote it)."""
    async with factory() as db:
        row = await db.get(RunTraceRow, (session_id, run_id))
    return row.summary if row is not None else None
