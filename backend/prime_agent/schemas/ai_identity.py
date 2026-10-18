"""AI identity DTOs (masterclass §7).

Immutable transfer objects — no HTTP, no secrets.
"""

from __future__ import annotations

from dataclasses import dataclass

from prime_agent.db.models.user import User


@dataclass(frozen=True, slots=True)
class TenantPrincipal:
    """Mapped AI-plane identity for a logged-in user (MVP: single-tenant-per-user)."""

    tenant_id: str
    principal_id: str

    @classmethod
    def from_user(cls, user: User) -> TenantPrincipal:
        """MVP mapper: both ids equal ``user.id`` until org/tenant tables exist."""
        return cls(tenant_id=user.id, principal_id=user.id)
