"""A session's subagents as a person reads them: what each was asked, how it is doing, what
it answered and what it cost (``supervisor.session.children`` says what a subagent is).

Everything here is read from what the sessions already record: each subagent's session row
(who started it, how, its task and its label), the ``SubagentStart`` and ``SubagentEnd`` events
in the journal of the session that started it (the call that started it, and each outcome as
that session read it), and the session index (its cost, which counts the subagents below it).
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Literal, cast

from sqlalchemy import select

from supervisor.agent.events import SubagentEnd, SubagentStart
from supervisor.db.models import EventRow, SessionIndexRow, SessionRow
from supervisor.journal.serde import type_name_of
from supervisor.temporal.contracts import CLOSED_STATUSES

__all__ = ["MAX_ANSWER_CHARS", "SubagentView", "subagent_views"]

MAX_ANSWER_CHARS: Final = 4_000
"""How much of a subagent's last answer a listing carries; its own transcript has it all."""

type Kind = Literal["foreground", "background", "continuable"]
type Mode = Literal["spawn", "fork"]
type Outcome = Literal["completed", "failed", "cancelled"]
type Status = Literal["starting", "running", "ready", "completed", "failed", "cancelled"]

_OUTCOMES: Final = frozenset({"completed", "failed", "cancelled"})


@dataclass(frozen=True, slots=True)
class SubagentView:
    id: str
    parent_id: str
    depth: int
    """1 for a subagent the session itself started, 2 for one of its subagents', and so on."""
    label: str | None
    task: str | None
    """What it was asked, as the call that started it said."""
    kind: Kind
    """foreground (its call waited for it), background, or continuable (it stays for more)."""
    mode: Mode
    """spawn (a fresh conversation) or fork (it carried its parent's)."""
    status: Status
    """``running`` while a run is open; ``ready`` for a continuable subagent waiting for its
    next message; ``starting`` before its first run is recorded; else how its run ended."""
    outcome: Outcome | None
    """How its last run ended, as githi grades it (``supervisor.session.children.status_of``)."""
    answer: str | None
    """Its last outcome as the session that started it read it (githi's graded text)."""
    answer_truncated: bool
    open: bool
    """Whether its session is still open (a continuable one takes messages while it is)."""
    call_id: str | None
    """The tool call of the session that started it."""
    cost_usd: float
    """What it spent, the subagents below it included."""
    created_at: datetime


@dataclass(frozen=True, slots=True)
class _Ended:
    outcome: Outcome | None
    output: str | None


def _status(*, busy: bool, kind: Kind, open_: bool, outcome: Outcome | None) -> Status:
    if busy:
        return "running"
    if kind == "continuable" and open_:
        return "ready"
    return outcome or "starting"


def _answer(text: str | None) -> tuple[str | None, bool]:
    if not text:
        return None, False
    if len(text) <= MAX_ANSWER_CHARS:
        return text, False
    return text[:MAX_ANSWER_CHARS].rstrip() + "…", True


async def subagent_views(
    db: Any, session_id: str, ids: Sequence[str], busy: Collection[str]
) -> list[SubagentView]:
    """The subagents ``ids`` below ``session_id``, in that order, with ``busy`` the ones with a
    run open; an id that is not a session (erased meanwhile) is left out."""
    if not ids:
        return []
    rows = {
        row.id: (row, idx)
        for row, idx in (
            await db.execute(
                select(SessionRow, SessionIndexRow)
                .outerjoin(SessionIndexRow, SessionIndexRow.session_id == SessionRow.id)
                .where(SessionRow.id.in_([session_id, *ids]))
            )
        ).all()
    }
    top = rows[session_id][0].subagent_depth if session_id in rows else 0
    ended = await _ends(db, {row.parent_session_id for row, _ in rows.values() if row.parent_session_id}, rows)
    out: list[SubagentView] = []
    for sid in ids:
        if sid not in rows:
            continue
        row, idx = rows[sid]
        kind = cast("Kind", row.subagent_kind or "foreground")
        open_ = row.status not in CLOSED_STATUSES
        call_id, last = ended.get(sid, (None, None))
        outcome = last.outcome if last is not None else None
        answer, cut = _answer(last.output if last is not None else None)
        out.append(
            SubagentView(
                id=sid,
                parent_id=row.parent_session_id or session_id,
                depth=row.subagent_depth - top,
                label=row.subagent_label or None,
                task=row.subagent_task,
                kind=kind,
                mode=cast("Mode", row.subagent_mode or "spawn"),
                status=_status(busy=sid in busy, kind=kind, open_=open_, outcome=outcome),
                outcome=outcome,
                answer=answer,
                answer_truncated=cut,
                open=open_,
                call_id=call_id,
                cost_usd=float(idx.total_cost) if idx is not None else 0.0,
                created_at=row.created_at,
            )
        )
    return out


async def _ends(
    db: Any, parents: Collection[str], rows: Collection[str]
) -> dict[str, tuple[str | None, _Ended | None]]:
    """For each subagent in ``rows``: the call that started it and its latest end, read from
    the journals of the ``parents`` that started them."""
    start, end = type_name_of(SubagentStart), type_name_of(SubagentEnd)
    found: dict[str, tuple[str | None, _Ended | None]] = {}
    first_runs: dict[str, str] = {}  # its first run's id → the subagent (an end that names none)
    events = await db.execute(
        select(EventRow.type, EventRow.payload)
        .where(EventRow.session_id.in_(parents), EventRow.type.in_((start, end)))
        .order_by(EventRow.session_id, EventRow.seq)
    )
    for kind, payload in events.all():
        run = str(payload.get("run_id") or "")
        child = str(payload.get("child_session_id") or "")
        if kind == start:
            if child in rows and child not in found:
                found[child] = (payload.get("call_id"), None)
                first_runs[run] = child
            continue
        child = child or first_runs.get(run, "")
        if child in rows:  # the latest end wins: a continuable subagent ends each turn
            reason = str(payload.get("stop_reason") or "")
            outcome = cast("Outcome", reason) if reason in _OUTCOMES else None
            found[child] = (found.get(child, (None, None))[0], _Ended(outcome, payload.get("output")))
    return found
