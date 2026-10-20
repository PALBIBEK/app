"""Customer runners: hands hosted on a tenant's own machines, which dial out to us.

A runner is a small process (``python -m supervisor.runner``) on a machine the
tenant controls, inside their network. It opens no port and needs no peering:
it long-polls the supervisor's API over HTTPS for the next command, carries it
out in a directory of its own, and posts the result back. The supervisor side
enqueues commands in Postgres (``runner_commands``) and waits for their result,
so any API replica serves the runner and any worker drives its hands.

A runner is registered per tenant under a name; its token is shown once and
stored only as a SHA-256. A sandbox profile with ``provider: runner`` names the
runner its hands run on; only sessions of the runner's own tenant reach it.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.db.models import RunnerCommandRow, RunnerRow

__all__ = [
    "LIVE_WITHIN_S",
    "OPS",
    "RunnerCommand",
    "RunnerHub",
    "RunnerOffline",
    "RunnerRefused",
    "current_hub",
    "set_hub",
]

OPS: Final = frozenset({"create", "exec", "read", "write", "exists", "list", "delete", "clear", "destroy"})
LIVE_WITHIN_S: Final = 30.0
"""A runner that has not polled for this long is offline: its hands are lost."""
_POLL_S: Final = 0.05
_POLL_MAX_S: Final = 0.5


class RunnerOffline(Exception):
    """The runner has not been seen lately, or did not answer in time."""


class RunnerRefused(Exception):
    """The runner carried out the command and reports it failed (``kind``: its error class)."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True, slots=True)
class RunnerCommand:
    id: str
    sandbox_id: str | None
    op: str
    args: dict[str, Any]


def _now_ms() -> int:
    return int(time.time() * 1000)


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class RunnerHub:
    __slots__ = ("_db",)

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._db = session_factory

    # --- registration -------------------------------------------------------------

    async def register(self, tenant_id: str, name: str) -> str:
        """Register (or re-key) a runner; returns its token, which is never shown again."""
        token = f"yr_{secrets.token_urlsafe(32)}"
        async with self._db() as db:
            row = await db.get(RunnerRow, (tenant_id, name))
            if row is None:
                db.add(RunnerRow(tenant_id=tenant_id, name=name, token_sha256=_digest(token), last_seen_ms=0))
            else:
                row.token_sha256 = _digest(token)  # the old token stops working
            await db.commit()
        return token

    async def verify(self, tenant_id: str, name: str, token: str) -> bool:
        async with self._db() as db:
            row = await db.get(RunnerRow, (tenant_id, name))
        return row is not None and hmac.compare_digest(row.token_sha256, _digest(token))

    async def runners(self, tenant_id: str) -> list[tuple[str, bool]]:
        """The tenant's runners and whether each is online."""
        async with self._db() as db:
            rows = (await db.scalars(select(RunnerRow).where(RunnerRow.tenant_id == tenant_id))).all()
        cutoff = _now_ms() - int(LIVE_WITHIN_S * 1000)
        return sorted((r.name, r.last_seen_ms >= cutoff) for r in rows)

    async def remove(self, tenant_id: str, name: str) -> bool:
        async with self._db() as db:
            row = await db.get(RunnerRow, (tenant_id, name))
            if row is None:
                return False
            await db.delete(row)
            await db.commit()
        return True

    async def online(self, tenant_id: str, name: str) -> bool:
        async with self._db() as db:
            row = await db.get(RunnerRow, (tenant_id, name))
        return row is not None and row.last_seen_ms >= _now_ms() - int(LIVE_WITHIN_S * 1000)

    async def last_seen_ms(self, tenant_id: str, name: str) -> int | None:
        """When the runner last polled (epoch ms; 0: never); ``None`` if it is not registered."""
        async with self._db() as db:
            row = await db.get(RunnerRow, (tenant_id, name))
        return None if row is None else int(row.last_seen_ms)

    # --- the runner's side ----------------------------------------------------------

    async def next_command(self, tenant_id: str, name: str, *, wait_s: float = 20.0) -> RunnerCommand | None:
        """Claim the runner's oldest pending command, waiting up to ``wait_s`` for one."""
        deadline = time.monotonic() + wait_s
        delay = _POLL_S
        while True:
            await self._touch(tenant_id, name)
            if (claimed := await self._claim(tenant_id, name)) is not None:
                return claimed
            if time.monotonic() >= deadline:
                return None
            await asyncio.sleep(delay)
            delay = min(delay * 2, _POLL_MAX_S)

    async def complete(
        self, tenant_id: str, name: str, command_id: str, *, ok: bool, result: dict[str, Any]
    ) -> bool:
        """Record a claimed command's outcome; False if it was not this runner's to finish."""
        async with self._db() as db:
            done = await db.execute(
                update(RunnerCommandRow)
                .where(
                    RunnerCommandRow.id == command_id,
                    RunnerCommandRow.tenant_id == tenant_id,
                    RunnerCommandRow.runner == name,
                    RunnerCommandRow.status == "claimed",
                )
                .values(status="done", result={"ok": ok, **result})
            )
            await db.commit()
        return bool(done.rowcount)  # type: ignore[attr-defined]

    async def _touch(self, tenant_id: str, name: str) -> None:
        async with self._db() as db:
            await db.execute(
                update(RunnerRow)
                .where(RunnerRow.tenant_id == tenant_id, RunnerRow.name == name)
                .values(last_seen_ms=_now_ms())
            )
            await db.commit()

    async def _claim(self, tenant_id: str, name: str) -> RunnerCommand | None:
        async with self._db() as db:
            for row in (
                await db.scalars(
                    select(RunnerCommandRow)
                    .where(
                        RunnerCommandRow.tenant_id == tenant_id,
                        RunnerCommandRow.runner == name,
                        RunnerCommandRow.status == "pending",
                    )
                    .order_by(RunnerCommandRow.created_ms)
                    .limit(5)
                )
            ).all():
                # Two polls of one runner race for it: the conditional update picks one.
                won = await db.execute(
                    update(RunnerCommandRow)
                    .where(RunnerCommandRow.id == row.id, RunnerCommandRow.status == "pending")
                    .values(status="claimed", claimed_ms=_now_ms())
                )
                await db.commit()
                if won.rowcount:  # type: ignore[attr-defined]
                    return RunnerCommand(row.id, row.sandbox_id, row.op, dict(row.args))
        return None

    # --- the supervisor's side -----------------------------------------------------------

    async def call(
        self,
        tenant_id: str,
        name: str,
        op: str,
        args: dict[str, Any] | None = None,
        *,
        sandbox_id: str | None = None,
        timeout_s: float = 300.0,
    ) -> dict[str, Any]:
        """Have the runner carry out ``op`` and return its result.

        Raises ``RunnerOffline`` when the runner is not polling (or never answers)
        and ``RunnerRefused`` when it reports the operation failed.
        """
        assert op in OPS, op
        if not await self.online(tenant_id, name):
            raise RunnerOffline(f"runner {name!r} is offline (not seen for {LIVE_WITHIN_S:.0f}s)")
        command_id = uuid.uuid4().hex
        async with self._db() as db:
            db.add(
                RunnerCommandRow(
                    id=command_id,
                    tenant_id=tenant_id,
                    runner=name,
                    sandbox_id=sandbox_id,
                    op=op,
                    args=args or {},
                    status="pending",
                    created_ms=_now_ms(),
                )
            )
            await db.commit()
        deadline = time.monotonic() + timeout_s
        delay = _POLL_S
        try:
            while time.monotonic() < deadline:
                async with self._db() as db:
                    row = await db.get(RunnerCommandRow, command_id)
                if row is not None and row.status == "done" and row.result is not None:
                    result = dict(row.result)
                    if not result.pop("ok", False):
                        raise RunnerRefused(str(result.get("kind") or "Error"), str(result.get("error") or ""))
                    return result
                if not await self.online(tenant_id, name):
                    raise RunnerOffline(f"runner {name!r} went offline during {op}")
                await asyncio.sleep(delay)
                delay = min(delay * 2, _POLL_MAX_S)
            raise RunnerOffline(f"runner {name!r} did not answer {op} within {timeout_s:.0f}s")
        finally:
            async with self._db() as db:  # done, or abandoned: either way it leaves the queue
                if (row := await db.get(RunnerCommandRow, command_id)) is not None:
                    await db.delete(row)
                    await db.commit()


_HUB: RunnerHub | None = None


def set_hub(hub: RunnerHub | None) -> None:
    """The process's hub (set at boot), which the ``runner`` sandbox provider uses."""
    global _HUB
    _HUB = hub


def current_hub() -> RunnerHub:
    if _HUB is None:
        raise RuntimeError("no runner hub in this process (the supervisor sets one at boot)")
    return _HUB
