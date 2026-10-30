"""How many sessions pin an artifact, from the sessions' own pins (``SessionPins``).

Counted only over the sessions the caller may see (``session.access``): usage of a
shipped artifact shows a tenant its own sessions, never anyone else's.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.catalog.core.types import ArtifactKind, ArtifactUsageStats, SessionPins
from supervisor.db.models import SessionRow
from supervisor.session.access import session_visibility

__all__ = ["ENDED_STATUSES", "artifact_usage"]

ENDED_STATUSES = frozenset({"terminated", "failed"})
"""Session statuses after which a session never runs (or pins) anything again."""

_ID_FIELD: dict[ArtifactKind, str] = {
    ArtifactKind.COMPOSITION: "composition_id",
    ArtifactKind.PROMPT_PACK: "prompt_pack_id",
    ArtifactKind.TOOL_SET: "tool_set_id",
    ArtifactKind.MEMORY_PROFILE: "memory_profile_id",
}
"""Where a session's pins name an artifact of each kind, for filtering in SQL.
(A sandbox profile may also be a named hand, so it is matched after loading.)"""


def _utc(at: datetime | None) -> datetime | None:
    if at is not None and at.tzinfo is None:  # SQLite returns naive datetimes, stored as UTC
        return at.replace(tzinfo=UTC)
    return at


async def artifact_usage(
    db_factory: async_sessionmaker[AsyncSession],
    *,
    tenant_id: str,
    principal_id: str | None,
    kind: ArtifactKind,
    artifact_id: str,
    digest: str | None = None,
) -> ArtifactUsageStats:
    """Sessions (visible to the caller) that pin ``artifact_id`` (at ``digest``, else any version)."""
    query = select(
        SessionRow.composition_pins,
        SessionRow.status,
        SessionRow.owner_principal_id,
        SessionRow.created_at,
        SessionRow.updated_at,
    ).where(
        SessionRow.tenant_id == tenant_id,
        session_visibility(tenant_id, principal_id),
        SessionRow.composition_pins.is_not(None),
        SessionRow.parent_session_id.is_(None),  # a subagent is part of its session
    )
    if (field := _ID_FIELD.get(kind)) is not None:
        query = query.where(SessionRow.composition_pins[field].as_string() == artifact_id)
    async with db_factory() as db:
        rows = (await db.execute(query)).all()

    per_digest: Counter[str] = Counter()
    last_use: dict[str, datetime] = {}
    principals: set[str] = set()
    sessions = 0
    active = 0
    first: datetime | None = None
    last: datetime | None = None
    for raw, status, owner, created_at, updated_at in rows:
        pinned = {
            d for k, name, d in SessionPins.from_dict(dict(raw)).refs() if k == kind and name == artifact_id
        }
        if digest is not None:
            pinned &= {digest}
        if not pinned:
            continue
        sessions += 1  # once, even when it pins the artifact twice (as its sandbox and a hand)
        started, touched = _utc(created_at), _utc(updated_at) or _utc(created_at)
        for d in pinned:
            per_digest[d] += 1
            if touched is not None and (d not in last_use or touched > last_use[d]):
                last_use[d] = touched
        principals.add(owner or tenant_id)
        active += status not in ENDED_STATUSES
        if started is not None and (first is None or started < first):
            first = started
        if touched is not None and (last is None or touched > last):
            last = touched

    if not per_digest:
        return ArtifactUsageStats.empty(kind, artifact_id, digest)
    epoch = datetime.min.replace(tzinfo=UTC)
    return ArtifactUsageStats(
        artifact_kind=kind,
        artifact_id=artifact_id,
        artifact_digest=digest,
        session_count=sessions,
        active_sessions=active,
        unique_principals=len(principals),
        first_used_at=first,
        last_used_at=last,
        versions=tuple(
            sorted(per_digest.items(), key=lambda item: last_use.get(item[0], epoch), reverse=True)
        ),
    )
