"""Scores on runs and spans: automatic checks, people's feedback, and quality by version.

A check is written when a run finishes (and again when its trace is rebuilt), replacing the
run's earlier checks. Feedback is one verdict per person per run or span: saying it again
replaces it. Each run's rollup row carries the counts that lists filter on.

Every score keeps the agent version (composition digest) that ran, so ``version_quality``
can compare versions on the same footing: that comparison is what promoting a better
version (later phases) is decided on.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from sqlalchemy import and_, case, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.db.models import RunTraceRow, SessionRow, TraceScoreRow
from supervisor.session.access import session_visibility
from supervisor.traces.checks import CHECKS_VERSION, run_checks
from supervisor.traces.model import RunTrace

__all__ = [
    "FEEDBACK",
    "Feedback",
    "clear_feedback",
    "run_scores",
    "score_out",
    "set_feedback",
    "version_quality",
    "write_checks",
]

Factory = async_sessionmaker[AsyncSession]

FEEDBACK = "feedback"
"""The name a person's verdict is stored under."""


def _now() -> int:
    return int(time.time() * 1000)


def _pins(session: SessionRow) -> tuple[str | None, str | None]:
    pins = session.composition_pins if isinstance(session.composition_pins, dict) else {}
    return pins.get("composition_id"), pins.get("composition_digest")


def score_out(row: TraceScoreRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "run_id": row.run_id,
        "span_id": row.span_id,
        "name": row.name,
        "source": row.source,
        "label": row.label,
        "value": row.value,
        "reason": row.reason,
        "comment": row.comment,
        "evaluator": row.evaluator,
        "principal_id": row.principal_id,
        "created_ms": row.created_ms,
    }


async def write_checks(db: AsyncSession, session: SessionRow, trace: RunTrace) -> int:
    """Replace the run's automatic checks with ``trace``'s; returns how many failed."""
    await db.execute(
        delete(TraceScoreRow).where(
            TraceScoreRow.session_id == trace.session_id,
            TraceScoreRow.run_id == trace.run_id,
            TraceScoreRow.source == "check",
        )
    )
    composition_id, digest = _pins(session)
    checks = run_checks(trace)
    now = _now()
    for c in checks:
        db.add(
            TraceScoreRow(
                id=str(uuid4()), tenant_id=session.tenant_id, session_id=trace.session_id, run_id=trace.run_id,
                span_id=c.span_id, name=c.name, source="check", label=c.label, value=1.0 if c.passed else 0.0,
                reason=c.reason, comment=None, evaluator=CHECKS_VERSION, principal_id=None,
                composition_id=composition_id, composition_digest=digest, created_ms=now,
            )
        )
    return sum(1 for c in checks if not c.passed)


async def _recount_feedback(db: AsyncSession, session_id: str, run_id: str) -> None:
    good, bad = (
        await db.execute(
            select(
                func.coalesce(func.sum(case((TraceScoreRow.label == "good", 1), else_=0)), 0),
                func.coalesce(func.sum(case((TraceScoreRow.label == "bad", 1), else_=0)), 0),
            ).where(
                TraceScoreRow.session_id == session_id,
                TraceScoreRow.run_id == run_id,
                TraceScoreRow.source == "person",
            )
        )
    ).one()
    row = await db.get(RunTraceRow, (session_id, run_id))
    if row is not None:
        row.feedback_good, row.feedback_bad = int(good), int(bad)


@dataclass(frozen=True)
class Feedback:
    good: bool
    span_id: str | None = None
    comment: str | None = None


async def set_feedback(
    factory: Factory, session: SessionRow, run_id: str, *, principal_id: str, feedback: Feedback
) -> dict[str, Any]:
    """The caller's verdict on the run (or one span), replacing any they gave before."""
    composition_id, digest = _pins(session)
    async with factory() as db, db.begin():
        await db.execute(
            delete(TraceScoreRow).where(
                TraceScoreRow.session_id == session.id,
                TraceScoreRow.run_id == run_id,
                TraceScoreRow.source == "person",
                TraceScoreRow.principal_id == principal_id,
                TraceScoreRow.span_id.is_(None) if feedback.span_id is None else TraceScoreRow.span_id == feedback.span_id,
            )
        )
        row = TraceScoreRow(
            id=str(uuid4()), tenant_id=session.tenant_id, session_id=session.id, run_id=run_id,
            span_id=feedback.span_id, name=FEEDBACK, source="person", label="good" if feedback.good else "bad",
            value=1.0 if feedback.good else 0.0, reason=None, comment=(feedback.comment or "").strip() or None,
            evaluator="person", principal_id=principal_id, composition_id=composition_id,
            composition_digest=digest, created_ms=_now(),
        )
        db.add(row)
        await db.flush()
        await _recount_feedback(db, session.id, run_id)
        return score_out(row)


async def clear_feedback(
    factory: Factory, session: SessionRow, run_id: str, *, principal_id: str, span_id: str | None
) -> bool:
    """Take back the caller's verdict; False when they had given none."""
    async with factory() as db, db.begin():
        gone = await db.execute(
            delete(TraceScoreRow).where(
                TraceScoreRow.session_id == session.id,
                TraceScoreRow.run_id == run_id,
                TraceScoreRow.source == "person",
                TraceScoreRow.principal_id == principal_id,
                TraceScoreRow.span_id.is_(None) if span_id is None else TraceScoreRow.span_id == span_id,
            )
        )
        await _recount_feedback(db, session.id, run_id)
        return bool(getattr(gone, "rowcount", 0))


async def run_scores(factory: Factory, session_id: str, run_id: str) -> list[dict[str, Any]]:
    """Every score on the run and its spans: checks first, then people's, oldest first."""
    async with factory() as db:
        rows = (
            await db.execute(
                select(TraceScoreRow)
                .where(TraceScoreRow.session_id == session_id, TraceScoreRow.run_id == run_id)
                .order_by(TraceScoreRow.source.asc(), TraceScoreRow.created_ms.asc())
            )
        ).scalars().all()
    return [score_out(r) for r in rows]


async def version_quality(
    factory: Factory, *, tenant_id: str, principal_id: str | None, composition_id: str
) -> list[dict[str, Any]]:
    """For each version of a composition the caller ran: how its finished runs went."""
    finished = RunTraceRow.status.notin_(("running", "awaiting_approval"))
    query = (
        select(
            RunTraceRow.composition_digest,
            func.count().label("runs"),
            func.sum(case((RunTraceRow.status == "ok", 1), else_=0)).label("answered"),
            func.sum(case((RunTraceRow.status.in_(("error", "budget_exceeded", "interrupted")), 1), else_=0)).label(
                "failed"
            ),
            func.sum(case((RunTraceRow.checks_failed > 0, 1), else_=0)).label("runs_failing_checks"),
            func.sum(RunTraceRow.feedback_good).label("feedback_good"),
            func.sum(RunTraceRow.feedback_bad).label("feedback_bad"),
            func.avg(RunTraceRow.duration_ms).label("avg_duration_ms"),
            func.avg(RunTraceRow.cost).label("avg_cost"),
            func.max(RunTraceRow.started_ms).label("last_run_ms"),
        )
        .join(SessionRow, SessionRow.id == RunTraceRow.session_id)
        .where(
            and_(SessionRow.tenant_id == tenant_id, session_visibility(tenant_id, principal_id)),
            RunTraceRow.composition_id == composition_id,
            finished,
        )
        .group_by(RunTraceRow.composition_digest)
        .order_by(func.max(RunTraceRow.started_ms).desc())
    )
    async with factory() as db:
        rows = (await db.execute(query)).all()
    return [
        {
            "composition_digest": r.composition_digest,
            "runs": int(r.runs),
            "answered": int(r.answered or 0),
            "failed": int(r.failed or 0),
            "runs_failing_checks": int(r.runs_failing_checks or 0),
            "feedback_good": int(r.feedback_good or 0),
            "feedback_bad": int(r.feedback_bad or 0),
            "avg_duration_ms": round(float(r.avg_duration_ms)) if r.avg_duration_ms is not None else None,
            "avg_cost": round(float(r.avg_cost or 0), 6),
            "last_run_ms": r.last_run_ms,
        }
        for r in rows
    ]
