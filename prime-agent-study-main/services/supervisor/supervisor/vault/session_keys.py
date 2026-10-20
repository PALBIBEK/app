"""One gateway key per session, kept in the vault and renewed before it expires.

Before the vault every runtime start minted a key: a resume, a takeover by
another worker, a restart each got a fresh key and a fresh budget, and a session
that stayed up past the key's hour lost model access. Now the key lives in the
vault under the session; every worker that runs the session's brain reads the
same one, and ``current`` mints again only when none is stored or it is about to
expire.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from supervisor.vault.store import Owner, Secret, Vault

if TYPE_CHECKING:
    from supervisor.seams.services.virtual_keys import MintedVirtualKey

__all__ = ["GATEWAY_KEY", "RENEW_BEFORE_S", "SessionKeys"]

GATEWAY_KEY: Final = "gateway_key"
RENEW_BEFORE_S: Final = 600.0
"""Renew a key with less than this left, so no model call starts on a dying key."""


class SessionKeys:
    __slots__ = ("_locks", "_vault")

    def __init__(self, vault: Vault) -> None:
        self._vault = vault
        self._locks: dict[str, asyncio.Lock] = {}

    async def current(
        self, session_id: str, mint: Callable[[], MintedVirtualKey]
    ) -> tuple[str, bool]:
        """The session's key, and whether it was minted just now (a renewal or the first)."""
        owner = Owner.session(session_id)
        async with self._locks.setdefault(session_id, asyncio.Lock()):
            stored = await self._vault.get(owner, GATEWAY_KEY)
            if stored is not None and not stored.expires_within(RENEW_BEFORE_S):
                return stored.value, False
            # Minting is a blocking HTTP call; keep it off the event loop.
            minted = await asyncio.to_thread(mint)
            expires = datetime.fromtimestamp(minted.expires_at, UTC)
            if stored is None:
                # Two workers starting one session at once: keep one key, not two.
                kept = await self._vault.put_if_absent(
                    owner, GATEWAY_KEY, minted.token, expires_at=expires
                )
                return kept.value, kept.value == minted.token
            await self._vault.put(owner, GATEWAY_KEY, minted.token, expires_at=expires)
            return minted.token, True

    async def stored(self, session_id: str) -> Secret | None:
        return await self._vault.get(Owner.session(session_id), GATEWAY_KEY)

    async def forget(self, session_id: str) -> None:
        self._locks.pop(session_id, None)
        await self._vault.delete(Owner.session(session_id))
