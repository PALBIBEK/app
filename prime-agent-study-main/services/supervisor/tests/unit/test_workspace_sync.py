"""Two sessions on one scope: a session's sync commits only what it changed (F20).

Each session hydrates a copy of the scope (its base) and syncs back after its
turns. A session that hydrated earlier must not overwrite, or delete, what
another session wrote since: that is a conflict, never last-writer-wins.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from supervisor.db.engine import open_db

from supervisor.seams.providers.storage.memory_object_storage import MemoryObjectStorage
from supervisor.seams.providers.workspace.memory_workspace_store import (
    MemoryTreeIO,
    MemoryWorkspaceStore,
)
from supervisor.seams.providers.workspace.postgres_workspace_store import PostgresWorkspaceStore
from supervisor.seams.services.workspace_store import (
    CasConflict,
    WorkspaceStore,
    WorkspaceSyncReport,
)

SCOPE = "proj"


@pytest.fixture(params=["memory", "sql"])
async def store(request: pytest.FixtureRequest, tmp_path: Path) -> AsyncIterator[WorkspaceStore]:
    """Both stores share the sync; the SQL one (Postgres in production) runs on SQLite here."""
    objects = MemoryObjectStorage()
    if request.param == "memory":
        yield await _seed(MemoryWorkspaceStore(objects=objects))
        return
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'ws.db'}") as (_, factory):
        yield await _seed(PostgresWorkspaceStore(factory, objects))


async def _seed(store: WorkspaceStore) -> WorkspaceStore:
    await store.commit_blob(SCOPE, "shared.txt", b"v0")
    await store.commit_blob(SCOPE, "notes.txt", b"notes")
    return store


async def _session(store: WorkspaceStore) -> tuple[MemoryTreeIO, WorkspaceSyncReport]:
    tree = MemoryTreeIO()
    return tree, await store.hydrate(SCOPE, tree)


async def _content(store: WorkspaceStore, path: str) -> bytes | None:
    rec = await store.stat(SCOPE, path)
    return None if rec is None else await store.objects.get(rec.object_key)  # type: ignore[attr-defined]


async def test_a_later_sync_does_not_overwrite_a_write_it_never_saw(
    store: WorkspaceStore,
) -> None:
    a, a_base = await _session(store)
    b, b_base = await _session(store)
    a.files["shared.txt"] = b"from A"
    assert (await store.sync(SCOPE, a, base=a_base.base)).written == ("shared.txt",)

    b.files["shared.txt"] = b"from B"
    report = await store.sync(SCOPE, b, base=b_base.base)

    assert report.conflicts == ("shared.txt",) and report.written == ()
    assert await _content(store, "shared.txt") == b"from A"


async def test_a_session_does_not_delete_files_created_after_it_hydrated(
    store: WorkspaceStore,
) -> None:
    a, a_base = await _session(store)
    b, b_base = await _session(store)
    a.files["new.txt"] = b"A made this"
    await store.sync(SCOPE, a, base=a_base.base)

    b.files["other.txt"] = b"B made this"  # B never saw new.txt
    report = await store.sync(SCOPE, b, base=b_base.base)

    assert report.removed == () and report.conflicts == ()
    assert await _content(store, "new.txt") == b"A made this"
    assert await _content(store, "other.txt") == b"B made this"


async def test_untouched_files_are_left_alone_even_if_the_scope_moved_on(
    store: WorkspaceStore,
) -> None:
    a, a_base = await _session(store)
    b, b_base = await _session(store)
    a.files["shared.txt"] = b"from A"
    await store.sync(SCOPE, a, base=a_base.base)

    b.files["notes.txt"] = b"B's notes"  # B still holds the old shared.txt, unchanged
    report = await store.sync(SCOPE, b, base=b_base.base)

    assert report.written == ("notes.txt",) and report.conflicts == ()
    assert await _content(store, "shared.txt") == b"from A"


async def test_deleting_a_file_someone_else_changed_is_a_conflict(
    store: WorkspaceStore,
) -> None:
    a, a_base = await _session(store)
    b, b_base = await _session(store)
    a.files["shared.txt"] = b"from A"
    await store.sync(SCOPE, a, base=a_base.base)

    del b.files["shared.txt"]
    report = await store.sync(SCOPE, b, base=b_base.base)

    assert report.conflicts == ("shared.txt",) and report.removed == ()
    assert await _content(store, "shared.txt") == b"from A"


async def test_a_session_alone_writes_creates_and_deletes_and_its_base_follows(
    store: WorkspaceStore,
) -> None:
    tree, hydrated = await _session(store)
    tree.files["shared.txt"] = b"v1"
    tree.files["made.txt"] = b"new"
    del tree.files["notes.txt"]
    first = await store.sync(SCOPE, tree, base=hydrated.base)
    assert (first.written, first.removed, first.conflicts) == (
        ("made.txt", "shared.txt"),
        ("notes.txt",),
        (),
    )
    tree.files["shared.txt"] = b"v2"  # a second turn builds on the first sync's base
    second = await store.sync(SCOPE, tree, base=first.base)
    assert (second.written, second.conflicts) == (("shared.txt",), ())
    assert await _content(store, "notes.txt") is None
    assert await _content(store, "shared.txt") == b"v2"


async def test_without_a_base_nothing_is_overwritten_or_deleted(
    store: WorkspaceStore,
) -> None:
    tree = MemoryTreeIO({"shared.txt": b"stale", "fresh.txt": b"x"})
    report = await store.sync(SCOPE, tree, base=None)
    assert report.conflicts == ("shared.txt",)
    assert report.written == ("fresh.txt",) and report.removed == ()
    assert await _content(store, "notes.txt") == b"notes"


async def test_versions_keep_counting_through_a_delete(store: WorkspaceStore) -> None:
    """Else a version a session saw before the delete would match the revived file."""
    before = await store.stat(SCOPE, "shared.txt")
    assert before is not None
    await store.soft_delete(SCOPE, "shared.txt")
    revived = await store.commit_blob(SCOPE, "shared.txt", b"another file")
    assert revived.version > before.version


async def test_delete_is_compare_and_set(store: WorkspaceStore) -> None:
    with pytest.raises(CasConflict):
        await store.soft_delete(SCOPE, "shared.txt", expected_version=7)
    assert await _content(store, "shared.txt") == b"v0"


# -- the session's side: base carried between syncs, conflicts resolved durable-wins --


class _Sandbox:
    def __init__(self) -> None:
        self.tree = MemoryTreeIO()

    def as_tree_io(self) -> MemoryTreeIO:
        return self.tree


class _AllowAll:
    async def require(self, *_: object) -> None:
        return None


async def test_a_session_that_loses_a_race_gets_the_scopes_files_back(
    store: WorkspaceStore,
) -> None:
    from supervisor.seams.services import Principal
    from supervisor.seams.services.session_workspace import (
        SessionWorkspaceService,
        SessionWorkspaceState,
        WorkspaceConflictError,
    )
    from supervisor.seams.services.workspace_hydrator import StoreWorkspaceHydrator

    service = SessionWorkspaceService(authz=_AllowAll(), hydrator=StoreWorkspaceHydrator(store))  # type: ignore[arg-type]
    a, b = _Sandbox(), _Sandbox()
    states = [
        SessionWorkspaceState(scope_id=SCOPE, principal=Principal(id=p, tenant_id="t1"))
        for p in ("alice", "bob")
    ]
    for state, box in zip(states, (a, b), strict=True):
        await service.ensure_hydrated(state, box)  # type: ignore[arg-type]

    a.tree.files["shared.txt"] = b"from A"
    service.mark_dirty(states[0])
    await service.sync_if_dirty(states[0], a)  # type: ignore[arg-type]
    b.tree.files["shared.txt"] = b"from B"
    service.mark_dirty(states[1])
    with pytest.raises(WorkspaceConflictError) as lost:
        await service.sync_if_dirty(states[1], b)  # type: ignore[arg-type]
    assert lost.value.conflicts == ("shared.txt",)

    await service.recover_cas_conflict(states[1], b)  # type: ignore[arg-type]
    assert b.tree.files["shared.txt"] == b"from A"
    b.tree.files["shared.txt"] = b"B, after seeing A"  # B's next edit builds on A's
    service.mark_dirty(states[1])
    report = await service.sync_if_dirty(states[1], b)  # type: ignore[arg-type]
    assert report is not None and report.written == ("shared.txt",)
    assert await _content(store, "shared.txt") == b"B, after seeing A"
