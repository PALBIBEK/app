"""The reply a model is writing, as it streams: Pi Durable's ``PartialOutput``.

Best effort and outside the journal. The model step writes what it has so far (at
most a few times a second); the event stream sends it to clients as a
``partial_output`` event with no id, so a reconnect never replays one; the row goes
once the reply is journalled. Losing a write only makes the stream less smooth.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.db.models import PartialOutputRow

__all__ = ["FLUSH_S", "Partial", "PartialOutputs", "PartialSink"]

logger = logging.getLogger(__name__)

FLUSH_S: Final = 0.25
"""How often a streaming reply is written: smooth to read, light on the database."""


@dataclass(frozen=True, slots=True)
class Partial:
    run_id: str
    step: int
    text: str
    thinking: str
    updated_ms: int


class PartialOutputs:
    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory

    async def put(self, session_id: str, run_id: str, step: int, text: str, thinking: str) -> None:
        async with self._factory() as db:
            await db.merge(
                PartialOutputRow(
                    session_id=session_id,
                    run_id=run_id,
                    step=step,
                    text=text,
                    thinking=thinking,
                    updated_ms=int(time.time() * 1000),
                )
            )
            await db.commit()

    async def clear(self, session_id: str) -> None:
        async with self._factory() as db:
            await db.execute(delete(PartialOutputRow).where(PartialOutputRow.session_id == session_id))
            await db.commit()

    async def get(self, session_id: str) -> Partial | None:
        async with self._factory() as db:
            row = await db.get(PartialOutputRow, session_id)
            if row is None:
                return None
            return Partial(row.run_id, row.step, row.text, row.thinking, int(row.updated_ms))


class PartialSink:
    """Collects a streaming reply from the model's thread; ``flushing`` writes it out.

    ``on_delta`` is called from the thread the model call runs in; the event loop
    writes the text so far every ``FLUSH_S`` while the call lasts, and once at the end.
    """

    def __init__(self, store: PartialOutputs, session_id: str, run_id: str, step: int) -> None:
        self._store, self._session_id, self._run_id, self._step = store, session_id, run_id, step
        self._lock = threading.Lock()
        self._text: list[str] = []
        self._thinking: list[str] = []
        self._version = 0
        self._written = 0

    def on_delta(self, kind: str, chunk: str) -> None:
        with self._lock:
            (self._thinking if kind == "thinking" else self._text).append(chunk)
            self._version += 1

    async def _flush(self) -> None:
        with self._lock:
            if self._version == self._written:
                return
            version, text, thinking = self._version, "".join(self._text), "".join(self._thinking)
        try:
            await self._store.put(self._session_id, self._run_id, self._step, text, thinking)
            self._written = version
        except Exception:
            logger.debug("partial output write failed session=%s", self._session_id, exc_info=True)

    async def flushing(self, call: asyncio.Future[Any]) -> None:
        """Write the reply so far until ``call`` is done."""
        while not call.done():
            await asyncio.wait({call}, timeout=FLUSH_S)
            await self._flush()
