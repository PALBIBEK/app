"""Erase a principal (a person leaving): their sessions, workspaces and, for a person who
is their own tenant, the tenant's secrets.

``DELETE /v1/principals/me/data`` acts as the caller. What goes:

- every session the caller owns (``owner_principal_id``, or the tenant when none was
  recorded and the caller is the tenant), each erased as ``supervisor.session.erase``
  erases one;
- every workspace scope the caller owns that nobody else is a member of, with its
  files, their versions and their stored bytes;
- the caller's memberships and grants on every other scope;
- when the caller is their own tenant (the product's one-person tenants), the tenant's
  secrets (``supervisor.vault``), which only they could have set, and its monthly spend.

A scope the caller owns with other members is shared work: it is handed to the member
who joined it first, with an owner's grants, and kept. Long-term memory is the memory
service's and is erased there by the API (``NotebookClient.erase_owner``).

Scope ownership is by principal id alone, as the grant store's checks are: principal
ids are unique across tenants.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy import ColumnElement, and_, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.db.models import (
    ScopeGrantRow,
    ScopeMembershipRow,
    ScopeRow,
    SessionRow,
    TenantSpendRow,
    VaultSecretRow,
    WorkspaceFileRow,
    WorkspaceFileVersionRow,
)
from supervisor.seams.services.authz import ALL_GRANTS
from supervisor.seams.services.object_storage import ObjectStorage
from supervisor.session.erase import SessionEraser

__all__ = ["PrincipalErased", "PrincipalEraser", "owned_by"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PrincipalErased:
    """What erasing a principal removed, or handed on (zero everywhere the second time)."""

    sessions: int = 0
    scopes: int = 0
    scopes_handed_on: int = 0
    workspace_files: int = 0
    secrets: int = 0


def owned_by(tenant_id: str, principal_id: str) -> ColumnElement[bool]:
    """Sessions of ``tenant_id`` that ``principal_id`` owns (a session with no recorded
    owner is the tenant's, as ``supervisor.session.access`` has it)."""
    return and_(
        SessionRow.tenant_id == tenant_id,
        func.coalesce(SessionRow.owner_principal_id, SessionRow.tenant_id) == principal_id,
    )


class PrincipalEraser:
    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        *,
        sessions: SessionEraser,
        objects: ObjectStorage | None,
        forget_secret: Callable[[str, str], None] | None = None,
    ) -> None:
        """``forget_secret(tenant_id, name)``: drop a deleted secret from a process cache."""
        self._db = factory
        self._sessions = sessions
        self._objects = objects
        self._forget_secret = forget_secret

    async def sessions_of(self, tenant_id: str, principal_id: str) -> list[str]:
        async with self._db() as db:
            return list(await db.scalars(select(SessionRow.id).where(owned_by(tenant_id, principal_id))))

    async def erase(self, tenant_id: str, principal_id: str) -> PrincipalErased:
        """Erase ``principal_id``'s data in ``tenant_id``. The caller ends their sessions
        first (``sessions_of``): a running one would go on writing what this removes."""
        sessions = await self.sessions_of(tenant_id, principal_id)
        for session_id in sessions:
            await self._sessions.erase(session_id)
        async with self._db() as db, db.begin():
            alone, shared = await self._owned_scopes(db, principal_id)
            keys = await self._stored_objects(db, alone)
            files = await self._delete_scopes(db, alone)
            for scope_id, heir in shared.items():
                await self._hand_on(db, scope_id, heir)
            await db.execute(delete(ScopeGrantRow).where(ScopeGrantRow.principal_id == principal_id))
            await db.execute(delete(ScopeMembershipRow).where(ScopeMembershipRow.principal_id == principal_id))
            secrets: list[str] = []
            if principal_id == tenant_id:
                secrets = await self._delete_tenant_secrets(db, tenant_id)
                await db.execute(delete(TenantSpendRow).where(TenantSpendRow.tenant_id == tenant_id))
        for name in secrets:
            if self._forget_secret is not None:
                self._forget_secret(tenant_id, name)
        for key in keys:
            await self._delete_object(key)
        return PrincipalErased(
            sessions=len(sessions),
            scopes=len(alone),
            scopes_handed_on=len(shared),
            workspace_files=files,
            secrets=len(secrets),
        )

    @staticmethod
    async def _owned_scopes(db: AsyncSession, principal_id: str) -> tuple[list[str], dict[str, str]]:
        """The scopes ``principal_id`` owns: those no one else is a member of, and the rest
        with the member each goes to (the one who joined first)."""
        owned = list(await db.scalars(select(ScopeRow.id).where(ScopeRow.owner_principal_id == principal_id)))
        others = await db.execute(
            select(ScopeMembershipRow.scope_id, ScopeMembershipRow.principal_id)
            .where(ScopeMembershipRow.scope_id.in_(owned), ScopeMembershipRow.principal_id != principal_id)
            .order_by(ScopeMembershipRow.created_at, ScopeMembershipRow.id)
        )
        heirs: dict[str, str] = {}
        for scope_id, member in others:
            heirs.setdefault(scope_id, member)
        return [scope_id for scope_id in owned if scope_id not in heirs], heirs

    @staticmethod
    async def _stored_objects(db: AsyncSession, scopes: Sequence[str]) -> list[str]:
        """The bytes of every version of every file of ``scopes`` (keys are per scope)."""
        versions = await db.scalars(
            select(WorkspaceFileVersionRow.object_key).where(WorkspaceFileVersionRow.scope_id.in_(scopes))
        )
        current = await db.scalars(select(WorkspaceFileRow.object_key).where(WorkspaceFileRow.scope_id.in_(scopes)))
        return sorted({key for key in [*versions, *current] if key})

    @staticmethod
    async def _delete_scopes(db: AsyncSession, scopes: Sequence[str]) -> int:
        """Delete ``scopes`` with their files, versions, members and grants; the files removed."""
        files = await db.scalar(
            select(func.count()).select_from(WorkspaceFileRow).where(WorkspaceFileRow.scope_id.in_(scopes))
        )
        # A session the caller does not own may still name one (a tenant-level caller's):
        # it keeps running without a workspace.
        await db.execute(update(SessionRow).where(SessionRow.scope_id.in_(scopes)).values(scope_id=None))
        for row in (WorkspaceFileVersionRow, WorkspaceFileRow, ScopeGrantRow, ScopeMembershipRow):
            await db.execute(delete(row).where(row.scope_id.in_(scopes)))
        await db.execute(delete(ScopeRow).where(ScopeRow.id.in_(scopes)))
        return int(files or 0)

    @staticmethod
    async def _hand_on(db: AsyncSession, scope_id: str, heir: str) -> None:
        """``heir`` owns ``scope_id`` from now on, with every grant an owner has."""
        await db.execute(update(ScopeRow).where(ScopeRow.id == scope_id).values(owner_principal_id=heir))
        held = set(
            await db.scalars(
                select(ScopeGrantRow.grant).where(ScopeGrantRow.scope_id == scope_id, ScopeGrantRow.principal_id == heir)
            )
        )
        for grant in ALL_GRANTS:
            if str(grant) not in held:
                db.add(ScopeGrantRow(id=uuid4().hex, scope_id=scope_id, principal_id=heir, grant=str(grant)))

    @staticmethod
    async def _delete_tenant_secrets(db: AsyncSession, tenant_id: str) -> list[str]:
        """Delete the tenant's secrets; their names."""
        removed = await db.scalars(
            delete(VaultSecretRow)
            .where(VaultSecretRow.owner_kind == "tenant", VaultSecretRow.owner_id == tenant_id)
            .returning(VaultSecretRow.name)
        )
        return list(removed)

    async def _delete_object(self, key: str) -> None:
        if self._objects is None:
            return
        try:
            await self._objects.delete(key)
        except Exception:
            logger.warning("could not delete object %s while erasing a principal", key, exc_info=True)
