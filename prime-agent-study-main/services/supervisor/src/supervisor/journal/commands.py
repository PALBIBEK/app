"""Durable HTTP command_id dedup. Harness-agnostic — retries, not agents."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.db.models import CommandRow


class CommandStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = session_factory

    async def claim(
        self,
        session_id: str,
        command_id: str,
        *,
        turn_id: str | None = None,
    ) -> tuple[bool, dict[str, Any]]:
        """Insert API response before send.

        Returns ``(True, response)`` on first claim, ``(False, stored)`` on duplicate.
        """
        tid = turn_id or uuid4().hex
        response: dict[str, Any] = {"turn_id": tid, "status": "accepted"}
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
