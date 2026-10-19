"""AuthzService implementation — fail-closed grant checks (WP-M4)."""

from __future__ import annotations

from supervisor.seams.services.authz import (
    AuthzService,
    Grant,
    GrantDenied,
    GrantStore,
    NotScopeMember,
    Principal,
)

__all__ = [
    "StoreAuthzService",
    "as_authz_service",
]


class StoreAuthzService:
    """Collaborator: membership + grant lookup via GrantStore."""

    def __init__(self, store: GrantStore) -> None:
        self._store = store

    async def check(
        self, principal: Principal, scope_id: str, grant: Grant
    ) -> bool:
        if not principal.id:
            return False
        if not await self._store.is_member(principal.id, scope_id):
            return False
        return await self._store.has_grant(principal.id, scope_id, grant)

    async def require(
        self, principal: Principal, scope_id: str, grant: Grant
    ) -> Principal:
        if not principal.id:
            raise GrantDenied(
                principal_id="",
                scope_id=scope_id,
                grant=str(grant),
            )
        if not await self._store.is_member(principal.id, scope_id):
            raise NotScopeMember(principal_id=principal.id, scope_id=scope_id)
        if not await self._store.has_grant(principal.id, scope_id, grant):
            raise GrantDenied(
                principal_id=principal.id,
                scope_id=scope_id,
                grant=str(grant),
            )
        return principal


def as_authz_service(store: GrantStore) -> AuthzService:
    return StoreAuthzService(store)
