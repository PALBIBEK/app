"""The vault: sealed at rest, bound to its slot, rotatable, refused without keys in production."""

from __future__ import annotations

import asyncio
import base64
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from cryptography.exceptions import InvalidTag
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from supervisor.db.engine import open_db
from supervisor.db.models import VaultSecretRow
from supervisor.seams.services.virtual_keys import MintedVirtualKey
from supervisor.vault import Keyring, Owner, Vault, VaultKeyError
from supervisor.vault.session_keys import RENEW_BEFORE_S, SessionKeys

type Factory = async_sessionmaker[AsyncSession]

PG_URL = os.environ.get("SUPERVISOR_TEST_PG_URL", "")
BACKENDS = [
    "sqlite",
    pytest.param(
        "postgres", marks=pytest.mark.skipif(not PG_URL, reason="SUPERVISOR_TEST_PG_URL not set")
    ),
]


@pytest_asyncio.fixture(params=BACKENDS)
async def db(request: pytest.FixtureRequest, tmp_path: Path) -> AsyncIterator[Factory]:
    if request.param == "sqlite":
        async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'v.db'}") as (_, factory):
            yield factory
        return
    name = f"vault_{uuid.uuid4().hex[:10]}"  # its own database: other suites share the default
    admin = create_async_engine(PG_URL, isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        async with open_db(f"{PG_URL.rpartition('/')[0]}/{name}") as (_, factory):
            yield factory
    finally:
        async with admin.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        await admin.dispose()


def _ring(*ids: str) -> Keyring:
    keys = {key_id: key_id.encode().ljust(32, b".") for key_id in ids}
    return Keyring(keys, active=ids[0])


ALICE = Owner.session("s-alice")
BOB = Owner.session("s-bob")


async def test_round_trip_and_nothing_readable_at_rest(db: Factory) -> None:
    vault = Vault(db, _ring("k1"))
    await vault.put(ALICE, "gateway_key", "sk-very-secret")
    got = await vault.get(ALICE, "gateway_key")
    assert got is not None and got.value == "sk-very-secret"
    async with db() as s:
        row = (await s.scalars(select(VaultSecretRow))).one()
    assert b"sk-very-secret" not in row.ciphertext and row.key_id == "k1"
    assert await vault.get(BOB, "gateway_key") is None


async def test_a_ciphertext_moved_to_another_slot_does_not_open(db: Factory) -> None:
    vault = Vault(db, _ring("k1"))
    await vault.put(ALICE, "gateway_key", "alice's")
    await vault.put(BOB, "gateway_key", "bob's")
    async with db() as s:
        alice = await s.scalar(select(VaultSecretRow).where(VaultSecretRow.owner_id == ALICE.id))
        assert alice is not None
        await s.execute(
            update(VaultSecretRow)
            .where(VaultSecretRow.owner_id == BOB.id)
            .values(nonce=alice.nonce, ciphertext=alice.ciphertext)
        )
        await s.commit()
    with pytest.raises(InvalidTag):
        await vault.get(BOB, "gateway_key")


async def test_rotation_reads_old_rows_and_writes_with_the_new_key(db: Factory) -> None:
    await Vault(db, _ring("k1")).put(ALICE, "a", "old")
    rotated = Vault(db, _ring("k2", "k1"))
    got = await rotated.get(ALICE, "a")
    assert got is not None and got.value == "old"
    await rotated.put(ALICE, "a", "new")
    async with db() as s:
        assert (await s.scalars(select(VaultSecretRow.key_id))).one() == "k2"
    with pytest.raises(VaultKeyError):
        await Vault(db, _ring("k1")).get(ALICE, "a")


async def test_put_if_absent_has_one_winner(db: Factory) -> None:
    vault = Vault(db, _ring("k1"))
    results = await asyncio.gather(*(vault.put_if_absent(ALICE, "k", f"v{n}") for n in range(5)))
    stored = await vault.get(ALICE, "k")
    assert stored is not None and {r.value for r in results} == {stored.value}


async def test_expiry_names_and_delete(db: Factory) -> None:
    vault = Vault(db, _ring("k1"))
    soon = datetime.now(UTC) + timedelta(minutes=5)
    await vault.put(ALICE, "b", "x", expires_at=soon)
    await vault.put(ALICE, "a", "y")
    got = await vault.get(ALICE, "b")
    assert got is not None and got.expires_within(600) and not got.expires_within(60)
    assert await vault.names(ALICE) == ["a", "b"]
    assert await vault.delete(ALICE, "a") == 1
    assert await vault.delete(ALICE) == 1
    assert await vault.names(ALICE) == []


def test_keyring_from_env() -> None:
    k1, k2 = Keyring.new_key(), Keyring.new_key()
    ring = Keyring.from_env({"VAULT_KEYS": f"new:{k1}, old:{k2}"})
    assert ring.active == "new"
    assert Keyring.from_env({}).active == "dev"
    for env in ({"ENV": "production"}, {"ENVIRONMENT": "staging"}):
        with pytest.raises(VaultKeyError):
            Keyring.from_env(env)
    short = base64.b64encode(b"x" * 16).decode()
    for bad in ("nocolon", f"k:{short}", "k:!!notbase64"):
        with pytest.raises(VaultKeyError):
            Keyring.from_env({"VAULT_KEYS": bad})


class _Mints:
    def __init__(self, lifetime_s: float) -> None:
        self.count = 0
        self.lifetime_s = lifetime_s

    def __call__(self) -> MintedVirtualKey:
        self.count += 1
        expires = datetime.now(UTC).timestamp() + self.lifetime_s
        return MintedVirtualKey(f"id-{self.count}", f"token-{self.count}", expires, "s", "t")


async def test_a_session_key_is_reused_until_it_nears_expiry(db: Factory) -> None:
    keys = SessionKeys(Vault(db, _ring("k1")))
    mint = _Mints(3600)
    assert await keys.current("s1", mint) == ("token-1", True)
    # Another worker (another SessionKeys on the same vault) gets the same key.
    assert await SessionKeys(Vault(db, _ring("k1"))).current("s1", mint) == ("token-1", False)
    assert mint.count == 1

    dying = _Mints(RENEW_BEFORE_S - 1)
    assert await keys.current("s2", dying) == ("token-1", True)
    assert await keys.current("s2", dying) == ("token-2", True)  # renewed, not reused

    await keys.forget("s1")
    assert await keys.stored("s1") is None


async def test_workers_starting_one_session_at_once_keep_one_key(db: Factory) -> None:
    vault = Vault(db, _ring("k1"))
    mint = _Mints(3600)
    got = await asyncio.gather(*(SessionKeys(vault).current("s1", mint) for _ in range(4)))
    stored = await SessionKeys(vault).stored("s1")
    assert stored is not None and {token for token, _ in got} == {stored.value}
    assert sum(fresh for _, fresh in got) == 1
