"""Read-only views across a caller's sessions: the approvals inbox, sandboxes, usage.

Each view sees exactly the sessions the caller may see (``session.access``) and
reads only durable records (the database and the journal), so any API replica
answers the same, whichever worker holds a session's sandboxes right now.
"""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.agent.events import (
    Envelope,
    HandCheckpointed,
    HandLost,
    HandProvisioned,
    HandStopped,
    MessageEnd,
)
from supervisor.db.models import ApprovalRow, HandLeaseRow, SessionIndexRow, SessionRow
from supervisor.hands.keys import DEFAULT_HAND, hand_key
from supervisor.journal.reader import JournalReader
from supervisor.session.access import session_visibility

__all__ = [
    "InboxItem",
    "SandboxView",
    "UsageRow",
    "approval_inbox",
    "sandboxes",
    "usage",
]

Factory = async_sessionmaker[AsyncSession]


def _ms(at: datetime | None) -> int | None:
    if at is None:
        return None
    if at.tzinfo is None:  # SQLite returns naive datetimes; they are stored in UTC
        at = at.replace(tzinfo=UTC)
    return int(at.timestamp() * 1000)


def _agent_of(row: SessionRow) -> str:
    """What the session runs, as a person names it: its composition, else its harness version."""
    pins = row.composition_pins or {}
    return str(pins.get("composition_id") or row.harness_version or "")


# --- the approvals inbox --------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class InboxItem:
    approval_id: str
    session_id: str
    session_title: str | None
    agent: str
    prompt: str
    options: list[str]
    created_at_ms: int | None
    expires_at_ms: int | None


async def approval_inbox(
    db_factory: Factory, *, tenant_id: str, principal_id: str | None, limit: int = 200
) -> list[InboxItem]:
    """Every pending approval in the sessions the caller may see, oldest first."""
    async with db_factory() as db:
        rows = (
            await db.execute(
                select(ApprovalRow, SessionRow, SessionIndexRow.title)
                .join(SessionRow, SessionRow.id == ApprovalRow.session_id)
                .outerjoin(SessionIndexRow, SessionIndexRow.session_id == SessionRow.id)
                .where(
                    ApprovalRow.status == "pending",
                    SessionRow.tenant_id == tenant_id,
                    session_visibility(tenant_id, principal_id),
                )
                .order_by(ApprovalRow.created_at.asc())
                .limit(limit)
            )
        ).all()
    out: list[InboxItem] = []
    for approval, session, title in rows:
        created = _ms(approval.created_at)
        out.append(
            InboxItem(
                approval_id=approval.approval_id,
                session_id=approval.session_id,
                session_title=title,
                agent=_agent_of(session),
                prompt=approval.prompt,
                options=list(approval.options or []),
                created_at_ms=created,
                expires_at_ms=created + int(float(approval.timeout_s) * 1000) if created is not None else None,
            )
        )
    return out


# --- sandboxes ------------------------------------------------------------------------------

HAND_EVENTS = ["HandProvisioned", "HandCheckpointed", "HandStopped", "HandLost"]

type HandState = Literal["running", "stopped", "lost", "not_started"]


@dataclass(slots=True)
class SandboxView:
    session_id: str
    hand: str
    state: HandState
    sandbox_id: str | None = None
    provider: str | None = None
    since_ms: int | None = None
    cause: str | None = None
    last_checkpoint_ms: int | None = None
    checkpoint_files: int = 0
    processes: list[str] = field(default_factory=list)
    reason: str | None = None
    logs_ref: str | None = None
    lease_holder: str | None = None
    lease_expires_ms: int | None = None


def fold_hands(session_id: str, events: Sequence[Envelope], pinned: Sequence[str] = ()) -> dict[str, SandboxView]:
    """Each hand's current state from its journal events (oldest first).

    The last of provisioned / stopped / lost decides the state; checkpoints
    update what was saved. Hands a composition pins that never started are
    listed as ``not_started``.
    """
    views: dict[str, SandboxView] = {
        name: SandboxView(session_id=session_id, hand=name, state="not_started") for name in pinned
    }
    for env in events:
        p = env.payload
        hand = getattr(p, "hand", DEFAULT_HAND) or DEFAULT_HAND
        v = views.setdefault(hand, SandboxView(session_id=session_id, hand=hand, state="not_started"))
        if isinstance(p, HandProvisioned):
            v.state, v.sandbox_id, v.provider, v.cause = "running", p.hand_id, p.provider, p.cause
            v.since_ms, v.reason, v.logs_ref = env.ts_ms, None, None
        elif isinstance(p, HandCheckpointed):
            v.last_checkpoint_ms, v.checkpoint_files, v.processes = env.ts_ms, int(p.files), list(p.processes)
        elif isinstance(p, HandStopped):
            v.state, v.since_ms, v.reason = "stopped", env.ts_ms, p.reason
            v.provider = v.provider or p.provider
        elif isinstance(p, HandLost):
            v.state, v.since_ms, v.reason, v.logs_ref = "lost", env.ts_ms, p.reason, p.logs_ref
            v.provider = v.provider or p.provider
    return views


def _pinned_hands(row: SessionRow) -> list[str]:
    hands = (row.composition_pins or {}).get("hands") or {}
    names = list(hands) if isinstance(hands, dict) else [h[0] for h in hands if h]
    return [str(n) for n in names]


async def sandboxes(
    db_factory: Factory,
    reader: JournalReader,
    *,
    tenant_id: str,
    principal_id: str | None,
    session_id: str | None = None,
    include_ended: bool = False,
) -> list[SandboxView]:
    """The sandboxes of one session, or of every open session the caller may see."""
    async with db_factory() as db:
        query = select(SessionRow).where(
            SessionRow.tenant_id == tenant_id, session_visibility(tenant_id, principal_id)
        )
        if session_id is not None:
            query = query.where(SessionRow.id == session_id)
        elif not include_ended:
            query = query.where(SessionRow.status.not_in(["terminated", "failed"]))
        sessions = list((await db.execute(query.limit(500))).scalars().all())
        keys = {
            hand_key(s.id, None if name == DEFAULT_HAND else name): (s.id, name)
            for s in sessions
            for name in {DEFAULT_HAND, *_pinned_hands(s)}
        }
        now = int(time.time() * 1000)
        leases = (
            (
                await db.execute(
                    select(HandLeaseRow).where(
                        HandLeaseRow.hand_key.in_(list(keys)),
                        HandLeaseRow.holder.is_not(None),
                        HandLeaseRow.expires_ms > now,
                    )
                )
            ).scalars().all()
            if keys
            else []
        )
    events = await reader.read_types([s.id for s in sessions], HAND_EVENTS)
    by_session: dict[str, list[Envelope]] = defaultdict(list)
    for env in events:
        by_session[env.session_id].append(env)
    views: dict[tuple[str, str], SandboxView] = {}
    for s in sessions:
        for hand, view in fold_hands(s.id, by_session.get(s.id, []), _pinned_hands(s)).items():
            views[(s.id, hand)] = view
    for lease in leases:
        if (where := keys.get(lease.hand_key)) is not None and where in views:
            views[where].lease_holder, views[where].lease_expires_ms = lease.holder, int(lease.expires_ms)
    return list(views.values())


# --- usage ----------------------------------------------------------------------------------

type GroupBy = Literal["agent", "day", "session"]


@dataclass(slots=True)
class UsageRow:
    key: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0
    calls: int = 0
    sessions: set[str] = field(default_factory=set)

    def out(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.input_tokens + self.output_tokens,
            "cost": round(self.cost, 8),
            "model_calls": self.calls,
            "sessions": len(self.sessions),
        }


def _day(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=UTC).strftime("%Y-%m-%d")


async def usage(
    db_factory: Factory,
    reader: JournalReader,
    *,
    tenant_id: str,
    principal_id: str | None,
    since_ms: int,
    until_ms: int,
    by: GroupBy = "day",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Tokens and cost of every model call in the window, grouped; and the totals.

    Read from each call's journalled usage (the same numbers runs report), for
    the sessions the caller may see.
    """
    async with db_factory() as db:
        sessions = {
            s.id: s
            for s in (
                await db.execute(
                    select(SessionRow).where(
                        SessionRow.tenant_id == tenant_id, session_visibility(tenant_id, principal_id)
                    )
                )
            ).scalars().all()
        }
    events = await reader.read_types(list(sessions), ["MessageEnd"], since_ms=since_ms, until_ms=until_ms)
    groups: dict[str, UsageRow] = {}
    total = UsageRow(key="total")
    for env in events:
        p = env.payload
        if not isinstance(p, MessageEnd) or p.role != "assistant" or p.usage is None:
            continue
        if by == "day":
            key = _day(env.ts_ms)
        elif by == "agent":
            key = _agent_of(sessions[env.session_id])
        else:
            key = env.session_id
        for row in (groups.setdefault(key, UsageRow(key=key)), total):
            row.input_tokens += int(p.usage.input_tokens)
            row.output_tokens += int(p.usage.output_tokens)
            row.cost += float(p.usage.cost_total)
            row.calls += 1
            row.sessions.add(env.session_id)
    ordered = sorted(groups.values(), key=lambda r: r.key) if by == "day" else sorted(groups.values(), key=lambda r: -r.cost)
    return [r.out() for r in ordered], total.out()
