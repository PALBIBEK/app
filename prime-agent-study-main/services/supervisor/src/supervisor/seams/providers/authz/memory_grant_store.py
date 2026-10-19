"""In-memory GrantStore for WP-M4 unit tests (no IdP / no Postgres required)."""

from __future__ import annotations

from dataclasses import dataclass, field

from supervisor.seams.services.authz import ALL_GRANTS, Grant, GrantStore


@dataclass
class MemoryGrantStore:
    """Fake memberships + grants. Structurally implements GrantStore."""

    _members: set[tuple[str, str]] = field(default_factory=set)
    _grants: set[tuple[str, str, Grant]] = field(default_factory=set)

    async def is_member(self, principal_id: str, scope_id: str) -> bool:
        return (principal_id, scope_id) in self._members

    async def has_grant(
        self, principal_id: str, scope_id: str, grant: Grant
    ) -> bool:
        return (principal_id, scope_id, grant) in self._grants

    async def list_grants(
        self, principal_id: str, scope_id: str
    ) -> frozenset[Grant]:
        return frozenset(
            g
            for (pid, sid, g) in self._grants
            if pid == principal_id and sid == scope_id
        )

    async def add_member(self, principal_id: str, scope_id: str) -> None:
        self._members.add((principal_id, scope_id))

    async def remove_member(self, principal_id: str, scope_id: str) -> None:
        self._members.discard((principal_id, scope_id))
        self._grants = {
            row
            for row in self._grants
            if not (row[0] == principal_id and row[1] == scope_id)
        }

    async def grant(
        self, principal_id: str, scope_id: str, grant: Grant
    ) -> None:
        self._members.add((principal_id, scope_id))
        self._grants.add((principal_id, scope_id, grant))

    async def revoke(
        self, principal_id: str, scope_id: str, grant: Grant
    ) -> None:
        self._grants.discard((principal_id, scope_id, grant))

    async def list_member_ids(self, scope_id: str) -> list[str]:
        return sorted(pid for pid, sid in self._members if sid == scope_id)

    async def bootstrap_owner(
        self, principal_id: str, scope_id: str
    ) -> None:
        await self.add_member(principal_id, scope_id)
        for g in ALL_GRANTS:
            await self.grant(principal_id, scope_id, g)


def as_grant_store(store: MemoryGrantStore) -> GrantStore:
    return store
