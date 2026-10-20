"""Lending a session's hand: one holder at a time, fenced.

A session's hand can be lent (to a background subagent): while it is, the
session's own calls on it are refused (``HAND_LENT``), and only the holder's
calls run. Each lease gets the next fencing token; a holder's calls carry it,
and a call whose token is not the hand's current one is refused
(``LEASE_ENDED``). So a holder that was slow, partitioned or restarted after
its lease expired, or was taken back, can never act on the hand again, even
if it never learnt the lease ended.

Leases live in Postgres (``hand_leases``), so every worker agrees, and they
expire: a holder renews by acting, and a holder that died frees the hand when
its lease runs out.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Final

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.db.models import HandLeaseRow

__all__ = ["LEASE_TTL_S", "Borrower", "HandLeases", "Lease"]

LEASE_TTL_S: Final = 15 * 60
"""How long a lease lasts without its holder acting on the hand."""


@dataclass(frozen=True, slots=True)
class Lease:
    hand_key: str
    holder: str
    token: int
    expires_ms: int


@dataclass(frozen=True, slots=True)
class Borrower:
    """Who is calling a tool on a borrowed hand: the lease's holder and token."""

    hand: str
    holder: str
    token: int

    def to_dict(self) -> dict[str, object]:
        return {"hand": self.hand, "holder": self.holder, "token": self.token}

    @classmethod
    def from_dict(cls, data: dict[str, object] | None) -> Borrower | None:
        if not data:
            return None
        return cls(str(data["hand"]), str(data["holder"]), int(str(data["token"])))


def _now_ms() -> int:
    return int(time.time() * 1000)


class HandLeases:
    __slots__ = ("_db",)

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._db = session_factory

    async def lend(self, hand_key: str, holder: str, *, ttl_s: float = LEASE_TTL_S) -> Lease | None:
        """Lend the hand to ``holder``: the new lease, or None if someone else holds it.

        Lending it again to the same holder renews that lease (same token).
        """
        expires = _now_ms() + int(ttl_s * 1000)
        if await self._first_lease(hand_key, holder, expires):
            return Lease(hand_key, holder, 1, expires)
        if (live := await self.current(hand_key)) is not None:
            return await self.renew(live, ttl_s=ttl_s) if live.holder == holder else None
        async with self._db() as db:
            result = await db.execute(
                update(HandLeaseRow)
                .where(
                    HandLeaseRow.hand_key == hand_key,
                    (HandLeaseRow.holder.is_(None)) | (HandLeaseRow.expires_ms <= _now_ms()),
                )
                .values(holder=holder, token=HandLeaseRow.token + 1, expires_ms=expires)
                .returning(HandLeaseRow.token)
            )
            token = result.scalar_one_or_none()
            await db.commit()
        return Lease(hand_key, holder, int(token), expires) if token is not None else None

    async def _first_lease(self, hand_key: str, holder: str, expires: int) -> bool:
        """The hand's first lease ever, if it has none yet (two lenders: one wins)."""
        async with self._db() as db:
            if await db.get(HandLeaseRow, hand_key) is not None:
                return False
            db.add(HandLeaseRow(hand_key=hand_key, holder=holder, token=1, expires_ms=expires))
            try:
                await db.commit()
            except IntegrityError:
                await db.rollback()
                return False
        return True

    async def current(self, hand_key: str) -> Lease | None:
        """The live lease on the hand, if one is."""
        async with self._db() as db:
            row = await db.scalar(
                select(HandLeaseRow).where(
                    HandLeaseRow.hand_key == hand_key,
                    HandLeaseRow.holder.is_not(None),
                    HandLeaseRow.expires_ms > _now_ms(),
                )
            )
        if row is None or row.holder is None:
            return None
        return Lease(row.hand_key, row.holder, int(row.token), int(row.expires_ms))

    async def holds(self, hand_key: str, holder: str, token: int) -> bool:
        """Whether ``holder``'s ``token`` is the hand's live lease (the fencing check)."""
        live = await self.current(hand_key)
        return live is not None and live.holder == holder and live.token == token

    async def renew(self, lease: Lease, *, ttl_s: float = LEASE_TTL_S) -> Lease | None:
        """Extend a live lease (its holder acted); None if it already ended."""
        expires = _now_ms() + int(ttl_s * 1000)
        async with self._db() as db:
            result = await db.execute(
                update(HandLeaseRow)
                .where(
                    HandLeaseRow.hand_key == lease.hand_key,
                    HandLeaseRow.holder == lease.holder,
                    HandLeaseRow.token == lease.token,
                    HandLeaseRow.expires_ms > _now_ms(),
                )
                .values(expires_ms=expires)
            )
            await db.commit()
        return Lease(lease.hand_key, lease.holder, lease.token, expires) if result.rowcount else None  # type: ignore[attr-defined]

    async def give_back(self, hand_key: str, holder: str, token: int) -> bool:
        """End ``holder``'s lease (the hand returns to its session); False if it had ended."""
        async with self._db() as db:
            result = await db.execute(
                update(HandLeaseRow)
                .where(
                    HandLeaseRow.hand_key == hand_key,
                    HandLeaseRow.holder == holder,
                    HandLeaseRow.token == token,
                )
                .values(holder=None, expires_ms=0)
            )
            await db.commit()
        return bool(result.rowcount)  # type: ignore[attr-defined]

    async def take_back(self, hand_key: str) -> None:
        """End whatever lease the hand has (its session needs it, or ends)."""
        async with self._db() as db:
            await db.execute(
                update(HandLeaseRow).where(HandLeaseRow.hand_key == hand_key).values(holder=None, expires_ms=0)
            )
            await db.commit()
