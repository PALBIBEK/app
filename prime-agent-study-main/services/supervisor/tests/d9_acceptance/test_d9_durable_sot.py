"""D9 acceptance — durable dual-SoT (postgres metadata + object bytes)."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from supervisor.db.engine import open_db
from supervisor.sandbox.local_process import LocalProcessProvider
from supervisor.seams.providers.memory_object_storage import MemoryObjectStorage
from supervisor.seams.providers.workspace_store_factory import (
    create_object_storage_for_workspace,
    create_workspace_store,
)
from supervisor.seams.workspace_store import CasConflict, ChecksumMismatch

ROOT = Path(__file__).resolve().parents[4]
COMPOSE = ROOT / "docker-compose.yml"
STAGING = ROOT / "deploy" / "staging" / "docker-compose.staging.yml"


def test_t_compose_defaults_postgres_and_object_storage() -> None:
    for path in (COMPOSE, STAGING):
        assert path.is_file(), path
        text = path.read_text(encoding="utf-8")
        assert "WORKSPACE_STORE: ${WORKSPACE_STORE:-postgres}" in text
        assert "OBJECT_STORAGE: seaweed" in text
        assert "DATABASE_URL: postgresql+asyncpg://" in text


def test_t_staging_refuses_memory_workspace_sot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENV", "staging")
    monkeypatch.setenv("WORKSPACE_STORE", "memory")
    with pytest.raises(RuntimeError, match="forbidden|postgres"):
        create_workspace_store()


def test_t_staging_refuses_memory_object_storage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENV", "staging")
    monkeypatch.setenv("OBJECT_STORAGE", "memory")
    with pytest.raises(RuntimeError, match="forbidden|seaweed|s3"):
        create_object_storage_for_workspace()


@pytest.mark.asyncio
async def test_t_restart_survives_same_files(tmp_path: Path) -> None:
    db = tmp_path / "d9.db"
    url = f"sqlite+aiosqlite:///{db}"
    objects = MemoryObjectStorage()
    scope = f"d9-{uuid4().hex[:12]}"
    payload = b"d9-restart-payload-v1"

    async with open_db(url) as (_, factory):
        store = create_workspace_store(
            "postgres", objects=objects, session_factory=factory
        )
        rec = await store.commit_blob(scope, "notes/hello.txt", payload)
        assert rec.version == 1
        assert await objects.get(rec.object_key) == payload

    async with open_db(url) as (_, factory2):
        store2 = create_workspace_store(
            "postgres", objects=objects, session_factory=factory2
        )
        provider = LocalProcessProvider(base_dir=tmp_path / "sandboxes")
        sandbox = await provider.create()
        report = await store2.hydrate(scope, sandbox.as_tree_io())
        assert "notes/hello.txt" in report.written
        assert await sandbox.read_file("notes/hello.txt") == payload


@pytest.mark.asyncio
async def test_t_cas_fail_closed_no_lww(tmp_path: Path) -> None:
    objects = MemoryObjectStorage()
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'cas.db'}") as (_, factory):
        store = create_workspace_store(
            "postgres", objects=objects, session_factory=factory
        )
        scope = uuid4().hex
        await store.commit_blob(scope, "x.txt", b"v1", expected_version=0)
        with pytest.raises(CasConflict):
            await store.commit_blob(scope, "x.txt", b"stale", expected_version=0)
        live = await store.stat(scope, "x.txt")
        assert live is not None
        assert await objects.get(live.object_key) == b"v1"


@pytest.mark.asyncio
async def test_t_hydrate_checksum_fail_closed(tmp_path: Path) -> None:
    objects = MemoryObjectStorage()
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'chk.db'}") as (_, factory):
        store = create_workspace_store(
            "postgres", objects=objects, session_factory=factory
        )
        scope = uuid4().hex
        rec = await store.commit_blob(scope, "corrupt.txt", b"good")
        await objects.put(rec.object_key, b"TAMPERED")
        provider = LocalProcessProvider(base_dir=tmp_path / "sbx")
        sandbox = await provider.create()
        with pytest.raises(ChecksumMismatch):
            await store.hydrate(scope, sandbox.as_tree_io())


@pytest.mark.asyncio
async def test_t_soft_delete_then_recreate(tmp_path: Path) -> None:
    objects = MemoryObjectStorage()
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'del.db'}") as (_, factory):
        store = create_workspace_store(
            "postgres", objects=objects, session_factory=factory
        )
        scope = uuid4().hex
        await store.commit_blob(scope, "a.txt", b"one", expected_version=0)
        await store.soft_delete(scope, "a.txt")
        again = await store.commit_blob(scope, "a.txt", b"two", expected_version=0)
        assert again.version == 1
        assert await objects.get(again.object_key) == b"two"


def test_t_dev_allows_explicit_memory_store(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("WORKSPACE_STORE", "memory")
    store = create_workspace_store()
    assert type(store).__name__ == "MemoryWorkspaceStore"
