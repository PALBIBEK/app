"""Virtual key client Protocol — HTTP adapter lives under providers/ (WP-M5)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class MintedVirtualKey:
    key_id: str
    token: str
    expires_at: float
    session_id: str
    tenant_id: str


@runtime_checkable
class VirtualKeyClient(Protocol):
    def mint(
        self,
        *,
        tenant_id: str,
        session_id: str,
        principal_id: str,
        allowed_models: list[str] | None = None,
        ttl_s: int = 3600,
        max_budget_usd: float = 5.0,
        scope_id: str | None = None,
    ) -> MintedVirtualKey: ...

    def revoke_session(self, session_id: str) -> int: ...
