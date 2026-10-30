"""A stand-in for the gateway's key minting (``VirtualKeyClient``)."""

from __future__ import annotations

import itertools
import time
from typing import Any

from supervisor.seams.services.virtual_keys import MintedVirtualKey


class FakeKeys:
    """Mints a distinct key per call (``token-1``, ``token-2``, ...) and remembers what was
    asked for. ``lifetime_s``: how long a key lives; 0, already expiring (renewed at once)."""

    def __init__(self, lifetime_s: float = 0.0) -> None:
        self.minted: list[dict[str, Any]] = []
        self.lifetime_s = lifetime_s
        self._ids = itertools.count(1)

    def mint(
        self, *, tenant_id: str, session_id: str, principal_id: str, **kw: Any
    ) -> MintedVirtualKey:
        n = next(self._ids)
        self.minted.append({"session_id": session_id, **kw})
        expires = time.time() + self.lifetime_s if self.lifetime_s else 0.0
        return MintedVirtualKey(f"vk-{n}", f"token-{n}", expires, session_id, tenant_id)

    def revoke_session(self, session_id: str) -> int:
        return 0
