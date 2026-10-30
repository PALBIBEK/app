"""How many event streams one person may hold open at once (per API process).

Each open stream holds a connection to the supervisor, which polls the session's journal
four times a second for it; nothing limited how many one person could open (a script, or a
page left open in many tabs). Past ``MAX_STREAMS_PER_USER`` the next is refused with 429
until one closes. The count is per process: behind N replicas a person may hold up to N
times as many.
"""

from __future__ import annotations

import os
from collections import Counter
from collections.abc import AsyncIterator
from typing import Final

from fastapi import HTTPException, status

__all__ = ["DEFAULT_MAX_STREAMS_PER_USER", "Slot", "StreamSlots", "max_streams_per_user"]

DEFAULT_MAX_STREAMS_PER_USER: Final = 20


def max_streams_per_user() -> int:
    """``MAX_STREAMS_PER_USER`` (default 20)."""
    raw = os.environ.get("MAX_STREAMS_PER_USER", "").strip()
    return int(raw) if raw.isdigit() and int(raw) > 0 else DEFAULT_MAX_STREAMS_PER_USER


class Slot:
    """One open stream counted against a person; ``release`` gives it back (once)."""

    def __init__(self, slots: StreamSlots, user_id: str) -> None:
        self._slots, self._user_id, self._held = slots, user_id, True

    def release(self) -> None:
        if self._held:
            self._held = False
            self._slots._give_back(self._user_id)  # noqa: SLF001

    async def serve(self, stream: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
        """``stream``, holding the slot until it ends however it ends."""
        try:
            async for chunk in stream:
                yield chunk
        finally:
            self.release()


class StreamSlots:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self._open: Counter[str] = Counter()

    def open_for(self, user_id: str) -> int:
        return self._open[user_id]

    def claim(self, user_id: str) -> Slot:
        """A slot for one more stream, or 429 when the person already holds ``limit``.
        Counted at once, so streams opened together cannot all slip past the limit."""
        if self._open[user_id] >= self.limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "error_code": "TOO_MANY_STREAMS",
                    "message": f"at most {self.limit} live session views at once; close one and try again",
                },
            )
        self._open[user_id] += 1
        return Slot(self, user_id)

    def _give_back(self, user_id: str) -> None:
        self._open[user_id] -= 1
        if self._open[user_id] <= 0:
            del self._open[user_id]
