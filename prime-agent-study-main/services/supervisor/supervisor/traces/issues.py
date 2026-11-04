"""Issues: the ways an agent's runs go wrong, grouped, counted, with examples.

Every failing verdict (a check that failed, a judge's fail, a person's "wrong") is grouped
by agent, by what judged it and by what it is about: "bash calls failed", "repeated read calls",
"judge 'answers the question' failed", "marked wrong". Each issue says how many runs it hit,
on which agent versions, when it was last seen, and shows the latest runs, so the person
fixing it starts from evidence. Grouping is by verdict and topic, not by text similarity:
it is exact, cheap, and its groups are the ones a fix targets.

One issue's runs (``key``, up to ``examples`` of them) are what a person saves to a dataset
to measure a fix on: the failures it is meant to fix, not runs picked by hand.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import and_, exists, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.db.models import RunTraceRow, SessionEvaluationRow, SessionRow, TraceScoreRow
from supervisor.session.access import session_visibility

__all__ = ["EXAMPLES", "SCAN_LIMIT", "issue_key", "issue_title", "list_issues"]

SCAN_LIMIT = 5_000
"""Failing verdicts read per listing, newest first; older ones fall out of the counts."""
EXAMPLES = 5

_FAILING = ("fail", "bad")


def issue_title(source: str, name: str, topic: str | None) -> str:
    t = topic or ""
    if source == "person":
        return "Marked wrong by people"
    if source == "judge":
        return f"Judge “{name}” failed"
    return {
        "finished": {
            "budget_exceeded": "Runs that ran out of budget",
            "interrupted": "Runs that stopped without finishing",
        }.get(t, f"Runs that failed ({t})" if t else "Runs that failed"),
        "tool_errors": f"{t} calls failed" if t else "Tool calls failed",
        "repeated_calls": f"Repeated {t} calls" if t else "Repeated calls",
        "approvals": f"Denied {t} calls" if t else "Denied calls",
        "tests_after_change": "Tests failing after a change",
    }.get(name, f"{name.replace('_', ' ')} failed")


def issue_key(composition_id: str | None, source: str, name: str, topic: str | None) -> str:
    return f"{composition_id or ''}:{source}:{name}:{topic or ''}"


@dataclass
class _Issue:
    composition_id: str | None
    source: str
    name: str
    topic: str | None
    runs: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    versions: dict[str | None, int] = field(default_factory=dict)
    last_seen_ms: int = 0


async def list_issues(
    factory: async_sessionmaker[AsyncSession],
    *,
    tenant_id: str,
    principal_id: str | None,
    composition_id: str | None = None,
    since_ms: int | None = None,
    limit: int = 30,
    exclude_sessions: list[str] | None = None,
    exclude_replays: bool = False,
    key: str | None = None,
    examples: int = EXAMPLES,
) -> list[dict[str, Any]]:
    """The caller's issues, the ones hitting the most runs first.

    ``exclude_sessions``: sessions whose runs are not evidence (an experiment's replays, which
    an optimizer must never learn from). ``exclude_replays``: leave out every session an
    experiment replayed in (``session_evaluations``). ``key``: only that issue, wherever it
    ranks. ``examples``: the latest runs shown per issue."""
    query = (
        select(TraceScoreRow, RunTraceRow.started_ms, RunTraceRow.input_preview, RunTraceRow.summary)
        .join(RunTraceRow, and_(RunTraceRow.session_id == TraceScoreRow.session_id,
                                RunTraceRow.run_id == TraceScoreRow.run_id))
        .join(SessionRow, SessionRow.id == TraceScoreRow.session_id)
        .where(SessionRow.tenant_id == tenant_id, session_visibility(tenant_id, principal_id))
        .where(TraceScoreRow.label.in_(_FAILING))
    )
    if composition_id:
        query = query.where(TraceScoreRow.composition_id == composition_id)
    if since_ms is not None:
        query = query.where(RunTraceRow.started_ms >= since_ms)
    if exclude_sessions:
        query = query.where(TraceScoreRow.session_id.not_in(exclude_sessions))
    if exclude_replays:
        query = query.where(~exists().where(SessionEvaluationRow.session_id == TraceScoreRow.session_id))
    async with factory() as db:
        found = (await db.execute(query.order_by(RunTraceRow.started_ms.desc()).limit(SCAN_LIMIT))).all()

    issues: dict[tuple[str | None, str, str, str | None], _Issue] = {}
    for score, started_ms, ask, summary in found:
        group = (score.composition_id, score.source, score.name, score.topic if score.source == "check" else None)
        issue = issues.setdefault(group, _Issue(*group))
        run_key = (score.session_id, score.run_id)
        if run_key not in issue.runs:
            issue.runs[run_key] = {
                "session_id": score.session_id,
                "run_id": score.run_id,
                "span_id": score.span_id,
                "started_ms": started_ms,
                "input": ask,
                "summary": summary,
                "reason": score.comment or score.reason,
                "composition_digest": score.composition_digest,
            }
            issue.versions[score.composition_digest] = issue.versions.get(score.composition_digest, 0) + 1
        issue.last_seen_ms = max(issue.last_seen_ms, started_ms or 0)

    groups = list(issues.values())
    if key is not None:
        groups = [i for i in groups if issue_key(i.composition_id, i.source, i.name, i.topic) == key]
    ranked = sorted(groups, key=lambda i: (len(i.runs), i.last_seen_ms), reverse=True)[:limit]
    return [
        {
            "key": issue_key(i.composition_id, i.source, i.name, i.topic),
            "composition_id": i.composition_id,
            "title": issue_title(i.source, i.name, i.topic),
            "source": i.source,
            "name": i.name,
            "topic": i.topic,
            "runs": len(i.runs),
            "last_seen_ms": i.last_seen_ms,
            "versions": [{"composition_digest": d, "runs": n} for d, n in sorted(i.versions.items(), key=lambda x: -x[1])],
            "examples": sorted(i.runs.values(), key=lambda r: -(r["started_ms"] or 0))[:examples],
        }
        for i in ranked
    ]
