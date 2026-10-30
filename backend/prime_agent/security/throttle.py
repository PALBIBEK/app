"""Sign-in throttling: too many failed passwords, or registrations, and the next waits.

Counters live in the database, so every API replica sees the same ones (``AuthThrottle``).
A login is counted three ways, each with its own limit (OWASP's guidance):

* the account from this address: a few guesses, then that pair waits;
* the account from anywhere: a distributed guess at one account, with a higher limit
  (so a stranger cannot lock someone out with a handful of tries);
* the address across accounts: credential stuffing.

A failed login for an email with no account counts the same, so the limits never say
which emails are registered. A successful login clears that account's counters.
Keys are hashed: no email or address is stored.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy import case, delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from prime_agent.db.models.auth_throttle import AuthThrottle

__all__ = [
    "ACCOUNT",
    "ACCOUNT_FROM_ADDRESS",
    "ADDRESS",
    "REGISTRATIONS",
    "Limit",
    "Throttle",
    "blocked_for",
    "clear",
    "count",
    "login_throttle",
    "registration_throttle",
]


@dataclass(frozen=True, slots=True)
class Limit:
    """At most ``attempts`` within ``window`` per ``name``d key."""

    name: str
    attempts: int
    window: timedelta


_WINDOW: Final = timedelta(minutes=15)
ACCOUNT_FROM_ADDRESS: Final = Limit("login.pair", 5, _WINDOW)
ACCOUNT: Final = Limit("login.account", 20, _WINDOW)
ADDRESS: Final = Limit("login.address", 100, _WINDOW)
REGISTRATIONS: Final = Limit("register.address", 10, timedelta(hours=1))


@dataclass(frozen=True, slots=True)
class Throttle:
    """The counters one attempt touches: ``(limit, key)`` pairs."""

    keys: tuple[tuple[Limit, str], ...]

    def only(self, *limits: Limit) -> Throttle:
        return Throttle(tuple((limit, key) for limit, key in self.keys if limit in limits))


def login_throttle(email: str, address: str) -> Throttle:
    """The counters a sign-in with ``email`` from ``address`` touches."""
    account = email.strip().lower()
    return Throttle((
        (ACCOUNT_FROM_ADDRESS, _key(ACCOUNT_FROM_ADDRESS, f"{account}\n{address}")),
        (ACCOUNT, _key(ACCOUNT, account)),
        (ADDRESS, _key(ADDRESS, address)),
    ))


def registration_throttle(address: str) -> Throttle:
    """The counter an account registration from ``address`` touches."""
    return Throttle(((REGISTRATIONS, _key(REGISTRATIONS, address)),))


def _key(limit: Limit, subject: str) -> str:
    return f"{limit.name}:{hashlib.sha256(subject.encode()).hexdigest()[:64]}"


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(moment: datetime) -> datetime:
    """SQLite hands back naive datetimes; they were written in UTC."""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


async def blocked_for(db: AsyncSession, throttle: Throttle) -> timedelta | None:
    """How long until the attempt may be made, or None when it may be made now."""
    now = _now()
    by_key = {key: limit for limit, key in throttle.keys}
    rows = (await db.execute(select(AuthThrottle).where(AuthThrottle.key.in_(by_key)))).scalars().all()
    waits = [
        _aware(row.window_started_at) + by_key[row.key].window - now
        for row in rows
        if row.count >= by_key[row.key].attempts
    ]
    live = [wait for wait in waits if wait > timedelta(0)]
    return max(live) if live else None


async def count(db: AsyncSession, throttle: Throttle) -> None:
    """Count one attempt on every counter (a window that has passed starts again at one)."""
    now = _now()
    for limit, key in throttle.keys:
        await _count_one(db, limit, key, now)
    await db.commit()


async def _count_one(db: AsyncSession, limit: Limit, key: str, now: datetime) -> None:
    current = AuthThrottle.window_started_at > now - limit.window
    bump = (
        update(AuthThrottle)
        .where(AuthThrottle.key == key)
        .values(
            count=case((current, AuthThrottle.count + 1), else_=1),
            window_started_at=case((current, AuthThrottle.window_started_at), else_=now),
        )
    )
    if (await db.execute(bump)).rowcount:  # type: ignore[attr-defined]
        return
    try:
        async with db.begin_nested():
            db.add(AuthThrottle(key=key, count=1, window_started_at=now))
    except IntegrityError:  # another replica counted the first attempt at the same moment
        await db.execute(bump)


async def clear(db: AsyncSession, throttle: Throttle) -> None:
    await db.execute(delete(AuthThrottle).where(AuthThrottle.key.in_([key for _, key in throttle.keys])))
    await db.commit()
