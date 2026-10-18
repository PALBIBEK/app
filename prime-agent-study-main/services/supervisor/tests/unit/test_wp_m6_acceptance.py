"""WP-M6 acceptance — scope session hydrate → write → sync → restart survives."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from supervisor.app import build_supervisor
from supervisor.db.engine import open_db
from supervisor.sandbox.env import sanitize_sandbox_env
from supervisor.sandbox.local_process import LocalProcessProvider
from supervisor.seams.authz import Grant, GrantDenied, NotScopeMember, Principal
from supervisor.seams.authorized_workspace import authorized_hydrate
from supervisor.seams.providers.grant_store_factory import (
    create_authz_service,
    create_grant_store,
)
from supervisor.seams.providers.memory_object_storage import MemoryObjectStorage
from supervisor.seams.providers.memory_workspace_store import MemoryWorkspaceStore
from supervisor.seams.types import ToolInvokeContext
from supervisor.seams.workspace_hydrator import StoreWorkspaceHydrator
from tests.support.session_env import make_catalog


async def _scoped_stack(tmp_path: Path):
    objects = MemoryObjectStorage()
    store = MemoryWorkspaceStore(objects=objects)
    hydrator = StoreWorkspaceHydrator(store)
    grant_store = create_grant_store("memory")
    authz = create_authz_service(store=grant_store)
    provider = LocalProcessProvider(
        base_dir=tmp_path / "sandboxes", hydrator=hydrator
    )
    return objects, store, hydrator, grant_store, authz, provider


@pytest.mark.asyncio
async def test_a1_scope_session_hydrates_seed_blob(tmp_path: Path) -> None:
    objects, store, hydrator, grant_store, authz, provider = await _scoped_stack(
        tmp_path
    )
    scope_id = uuid4().hex
    await store.commit_blob(scope_id, "seed.txt", b"seed-bytes")
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'a1.db'}") as (_, factory):
        state = build_supervisor(
            session_factory=factory,
            catalog=make_catalog(),
            blob_dir=tmp_path / "blobs",
            sandbox_provider=provider,
            authz=authz,
            grant_store=grant_store,
        )
        sid = await state.manager.create(
            tenant_id="t1",
            harness_version="githi-v1",
            runtime_name="githi",
            scope_id=scope_id,
            principal_id="t1",
        )
        sb = state.manager._sandboxes[sid]
        assert sb is not None
        assert await sb.as_tree_io().read_bytes("seed.txt") == b"seed-bytes"
        await state.manager.terminate(sid)


@pytest.mark.asyncio
async def test_a2_write_tool_sync_commits_object_and_version(tmp_path: Path) -> None:
    objects, store, hydrator, grant_store, authz, provider = await _scoped_stack(
        tmp_path
    )
    scope_id = uuid4().hex
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'a2.db'}") as (_, factory):
        state = build_supervisor(
            session_factory=factory,
            catalog=make_catalog(),
            blob_dir=tmp_path / "blobs",
            sandbox_provider=provider,
            authz=authz,
            grant_store=grant_store,
        )
        sid = await state.manager.create(
            tenant_id="t1",
            harness_version="githi-v1",
            runtime_name="githi",
            scope_id=scope_id,
            principal_id="t1",
        )
        ws = state.manager._workspace[sid]
        sb = state.manager._sandboxes[sid]
        rt = state.manager._registry.get(sid)
        assert rt is not None and sb is not None
        result = rt.seams.tool_provider.invoke(
            ToolInvokeContext(),
            "write",
            {"file_path": "out.txt", "content": "from-tool"},
        )
        assert result.ok is True
        assert ws.dirty is True
        report = await state.manager._workspace_svc.sync_if_dirty(ws, sb)
        assert report is not None and "out.txt" in report.written
        row = await store.stat(scope_id, "out.txt")
        assert row is not None and row.version == 1
        assert await objects.get(row.object_key) == b"from-tool"
        assert ws.dirty is False
        await state.manager.terminate(sid)


@pytest.mark.asyncio
async def test_a3_sync_soft_delete_invisible_on_rehydrate(tmp_path: Path) -> None:
    objects, store, _, _, _, provider = await _scoped_stack(tmp_path)
    del objects
    scope = "a3"
    await store.commit_blob(scope, "gone.txt", b"x", expected_version=0)
    await store.commit_blob(scope, "keep.txt", b"y", expected_version=0)
    sb = await provider.create()
    await sb.hydrate_workspace(scope)
    (sb.root / "gone.txt").unlink()
    report = await sb.sync_workspace(scope)
    assert "gone.txt" in report.removed
    sb2 = await provider.create()
    again = await sb2.hydrate_workspace(scope)
    assert "gone.txt" not in again.written
    assert (sb2.root / "keep.txt").read_bytes() == b"y"
    assert not (sb2.root / "gone.txt").exists()


@pytest.mark.asyncio
async def test_a4_file_survives_sandbox_restart(tmp_path: Path) -> None:
    objects, store, hydrator, grant_store, authz, provider = await _scoped_stack(
        tmp_path
    )
    scope_id = uuid4().hex
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'a4.db'}") as (_, factory):
        state = build_supervisor(
            session_factory=factory,
            catalog=make_catalog(),
            blob_dir=tmp_path / "blobs",
            sandbox_provider=provider,
            authz=authz,
            grant_store=grant_store,
        )
        sid = await state.manager.create(
            tenant_id="t1",
            harness_version="githi-v1",
            runtime_name="githi",
            scope_id=scope_id,
            principal_id="t1",
        )
        ws = state.manager._workspace[sid]
        sb = state.manager._sandboxes[sid]
        rt = state.manager._registry.get(sid)
        assert rt is not None and sb is not None
        rt.seams.tool_provider.invoke(
            ToolInvokeContext(),
            "write",
            {"file_path": "survive.txt", "content": "persist-me"},
        )
        await state.manager._workspace_svc.sync_if_dirty(ws, sb)
        row = await store.stat(scope_id, "survive.txt")
        assert row is not None
        committed_sha = row.content_sha256

        # Destroy sandbox cache; new sandbox must rehydrate committed bytes.
        await sb.destroy()
        state.manager._sandboxes[sid] = None
        ws.ready = False
        new_sb = await provider.create()
        state.manager._sandboxes[sid] = new_sb
        await state.manager._workspace_svc.ensure_hydrated(ws, new_sb, force=True)
        data = await new_sb.as_tree_io().read_bytes("survive.txt")
        assert data == b"persist-me"
        import hashlib

        assert hashlib.sha256(data).hexdigest() == committed_sha
        await state.manager.terminate(sid)


@pytest.mark.asyncio
async def test_a5_read_grant_hydrate_write_denied(tmp_path: Path) -> None:
    _, store, hydrator, grant_store, authz, provider = await _scoped_stack(tmp_path)
    scope = uuid4().hex
    await store.commit_blob(scope, "x.txt", b"x")
    await grant_store.add_member("reader", scope)
    await grant_store.grant("reader", scope, Grant.WORKSPACE_READ)
    sb = await provider.create()
    tree = sb.as_tree_io()
    principal = Principal(id="reader", tenant_id="t1")
    await authorized_hydrate(authz, hydrator, principal, scope, tree)
    assert await tree.read_bytes("x.txt") == b"x"
    with pytest.raises(GrantDenied):
        await authz.require(principal, scope, Grant.WORKSPACE_WRITE)
    stranger = Principal(id="stranger", tenant_id="t1")
    with pytest.raises(NotScopeMember):
        await authorized_hydrate(authz, hydrator, stranger, scope, tree)


@pytest.mark.asyncio
async def test_a6_path_escape_rejected_from_write_tool(tmp_path: Path) -> None:
    _, store, hydrator, grant_store, authz, provider = await _scoped_stack(tmp_path)
    scope_id = uuid4().hex
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'a6.db'}") as (_, factory):
        state = build_supervisor(
            session_factory=factory,
            catalog=make_catalog(),
            blob_dir=tmp_path / "blobs",
            sandbox_provider=provider,
            authz=authz,
            grant_store=grant_store,
        )
        sid = await state.manager.create(
            tenant_id="t1",
            harness_version="githi-v1",
            runtime_name="githi",
            scope_id=scope_id,
            principal_id="t1",
        )
        rt = state.manager._registry.get(sid)
        assert rt is not None
        result = rt.seams.tool_provider.invoke(
            ToolInvokeContext(),
            "write",
            {"file_path": "../escape.txt", "content": "nope"},
        )
        assert result.ok is False
        assert result.code == "TOOL_ERROR"
        await state.manager.terminate(sid)


def test_a7_sandbox_env_excludes_provider_secrets() -> None:
    env = sanitize_sandbox_env(
        {"OPENAI_API_KEY": "sk-x", "SAFE": "1"},
        base={
            "PATH": "/bin",
            "OPENROUTER_API_KEY": "sk-or",
            "MODEL_GATEWAY_TOKEN": "svc",
            "DATABASE_URL": "postgres://x",
        },
    )
    assert env.get("PATH") == "/bin"
    assert env.get("SAFE") == "1"
    for bad in (
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
        "MODEL_GATEWAY_TOKEN",
        "DATABASE_URL",
    ):
        assert bad not in env
