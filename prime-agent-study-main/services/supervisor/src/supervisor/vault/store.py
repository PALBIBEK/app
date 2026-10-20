"""Secrets by owner and name, sealed in the database."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.db.models import VaultSecretRow
from supervisor.vault.crypto import Keyring, Sealed

__all__ = ["Owner", "Secret", "Vault"]


@dataclass(frozen=True, slots=True)
class Owner:
    """Whose secret it is: one session's (its gateway key) or one tenant's (a tool's token)."""

    kind: Literal["session", "tenant"]
    id: str

    @classmethod
    def session(cls, session_id: str) -> Owner:
        return cls("session", session_id)

    @classmethod
    def tenant(cls, tenant_id: str) -> Owner:
        return cls("tenant", tenant_id)


@dataclass(frozen=True, slots=True)
class Secret:
    name: str
    value: str
    expires_at: datetime | None = None

    def expires_within(self, seconds: float, *, now: datetime | None = None) -> bool:
        if self.expires_at is None:
            return False
        moment = now or datetime.now(UTC)
        return (self.expires_at - moment).total_seconds() <= seconds


def _binding(owner: Owner, name: str) -> bytes:
    return f"{owner.kind}\x00{owner.id}\x00{name}".encode()


class Vault:
    __slots__ = ("_db", "_keys")

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], keys: Keyring) -> None:
        self._db = session_factory
        self._keys = keys

    async def put(
        self, owner: Owner, name: str, value: str, *, expires_at: datetime | None = None
    ) -> None:
        """Store (or replace) ``owner``'s secret ``name``."""
        sealed = self._keys.seal(value.encode(), binding=_binding(owner, name))
        async with self._db() as db:
            row = await self._row(db, owner, name)
            if row is None:
                row = VaultSecretRow(
                    id=uuid4().hex, owner_kind=owner.kind, owner_id=owner.id, name=name
                )
                db.add(row)
            row.key_id, row.nonce, row.ciphertext = sealed.key_id, sealed.nonce, sealed.ciphertext
            row.expires_at = expires_at
            row.updated_at = datetime.now(UTC)
            await db.commit()

    async def put_if_absent(
        self, owner: Owner, name: str, value: str, *, expires_at: datetime | None = None
    ) -> Secret:
        """Store unless someone stored it first; returns the secret that is stored now."""
        sealed = self._keys.seal(value.encode(), binding=_binding(owner, name))
        async with self._db() as db:
            db.add(
                VaultSecretRow(
                    id=uuid4().hex,
                    owner_kind=owner.kind,
                    owner_id=owner.id,
                    name=name,
                    key_id=sealed.key_id,
                    nonce=sealed.nonce,
                    ciphertext=sealed.ciphertext,
                    expires_at=expires_at,
                )
            )
            try:
                await db.commit()
            except IntegrityError:
                await db.rollback()
                existing = await self.get(owner, name)
                assert existing is not None, "a unique conflict means the row exists"
                return existing
        return Secret(name, value, expires_at)

    async def get(self, owner: Owner, name: str) -> Secret | None:
        async with self._db() as db:
            row = await self._row(db, owner, name)
        if row is None:
            return None
        plain = self._keys.open(
            Sealed(row.key_id, row.nonce, row.ciphertext), binding=_binding(owner, name)
        )
        expires = row.expires_at
        if expires is not None and expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)  # SQLite returns naive datetimes
        return Secret(name, plain.decode(), expires)

    async def names(self, owner: Owner) -> list[str]:
        async with self._db() as db:
            rows = await db.scalars(
                select(VaultSecretRow.name)
                .where(VaultSecretRow.owner_kind == owner.kind, VaultSecretRow.owner_id == owner.id)
                .order_by(VaultSecretRow.name)
            )
            return list(rows)

    async def delete(self, owner: Owner, name: str | None = None) -> int:
        """Delete one secret, or every secret ``owner`` has (``name`` None)."""
        query = delete(VaultSecretRow).where(
            VaultSecretRow.owner_kind == owner.kind, VaultSecretRow.owner_id == owner.id
        )
        if name is not None:
            query = query.where(VaultSecretRow.name == name)
        async with self._db() as db:
            result = await db.execute(query.returning(VaultSecretRow.id))
            removed = len(result.all())
            await db.commit()
        return removed

    @staticmethod
    async def _row(db: AsyncSession, owner: Owner, name: str) -> VaultSecretRow | None:
        return await db.scalar(
            select(VaultSecretRow).where(
                VaultSecretRow.owner_kind == owner.kind,
                VaultSecretRow.owner_id == owner.id,
                VaultSecretRow.name == name,
            )
        )
