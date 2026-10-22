"""Durable HTTP command_id dedup. Harness-agnostic — retries, not agents."""

from __future__ import annotations

import hashlib
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.db.models import CommandRow


class CommandConflict(Exception):
    """A command id already used for a different payload: an idempotency key is one request."""

    def __init__(self, command_id: str) -> None:
        super().__init__(f"command_id {command_id!r} was already used with a different message")
        self.command_id = command_id


def fingerprint(payload: str) -> str:
    """What a command id is bound to: a retry must carry the same payload."""
    return hashlib.sha256(payload.encode()).hexdigest()


def check_same_payload(command_id: str, stored: dict[str, Any], payload_fingerprint: str | None) -> None:
    """Raise ``CommandConflict`` if ``stored`` was recorded for a different payload."""
    recorded = stored.get("fingerprint")
    if payload_fingerprint and recorded and recorded != payload_fingerprint:
        raise CommandConflict(command_id)


class CommandStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = session_factory

    async def claim(
        self,
        session_id: str,
        command_id: str,
        *,
        turn_id: str | None = None,
        payload_fingerprint: str | None = None,
    ) -> tuple[bool, dict[str, Any]]:
        """Insert API response before send.

        Returns ``(True, response)`` on first claim, ``(False, stored)`` on duplicate;
        raises ``CommandConflict`` when the duplicate carried a different payload.
        """
        tid = turn_id or uuid4().hex
        response: dict[str, Any] = {"turn_id": tid, "status": "accepted"}
        if payload_fingerprint:
            response["fingerprint"] = payload_fingerprint
        row = CommandRow(
            session_id=session_id,
            command_id=command_id,
            turn_id=tid,
            response=response,
        )
        async with self._factory() as db:
            db.add(row)
            try:
                await db.commit()
            except IntegrityError:
                await db.rollback()
                existing = await db.get(CommandRow, (session_id, command_id))
                if existing is None:
                    raise
                check_same_payload(command_id, dict(existing.response), payload_fingerprint)
                return False, dict(existing.response)
        return True, response

    async def get(
        self, session_id: str, command_id: str
    ) -> dict[str, Any] | None:
        async with self._factory() as db:
            row = await db.get(CommandRow, (session_id, command_id))
            if row is None:
                return None
            return dict(row.response)

    async def by_turn(self, session_id: str, turn_id: str) -> CommandRow | None:
        """The command (message) that started run ``turn_id``."""
        async with self._factory() as db:
            return await db.scalar(
                select(CommandRow).where(CommandRow.session_id == session_id, CommandRow.turn_id == turn_id)
            )

    async def recent(self, session_id: str, *, limit: int = 50) -> list[CommandRow]:
        """The session's messages, newest first."""
        async with self._factory() as db:
            rows = await db.scalars(
                select(CommandRow)
                .where(CommandRow.session_id == session_id)
                .order_by(CommandRow.created_at.desc())
                .limit(limit)
            )
            return list(rows.all())

    async def mark(self, session_id: str, command_id: str, status: str) -> None:
        """Record a new status for a command's run (e.g. ``cancelled`` while it was queued)."""
        async with self._factory() as db:
            row = await db.get(CommandRow, (session_id, command_id))
            if row is not None:
                row.response = {**dict(row.response), "status": status}
                await db.commit()
