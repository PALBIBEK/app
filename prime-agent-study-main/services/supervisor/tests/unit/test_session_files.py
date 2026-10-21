"""Attachments, run inputs and artifacts (supervisor.session.files) with a real sandbox."""

from __future__ import annotations

import pytest

from supervisor.db.engine import open_db
from supervisor.sandbox.providers.local_process import LocalProcessProvider
from supervisor.seams.providers.storage.memory_object_storage import MemoryObjectStorage
from supervisor.seams.providers.workspace.memory_workspace_store import MemoryWorkspaceStore
from supervisor.seams.services.session_workspace import ScopeMirrorTree, tree_for_sandbox
from supervisor.session.files import FileNotInWorkspace, SessionFiles

SCOPE = "ws-1"
SID = "sess-1"


@pytest.fixture
async def env(tmp_path):
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'f.db'}") as (_engine, factory):
        store = MemoryWorkspaceStore(objects=MemoryObjectStorage())
        sandbox = await LocalProcessProvider(base_dir=tmp_path / "sbx").create()
        yield SessionFiles(factory, store), store, sandbox
        await sandbox.destroy()


async def test_an_attachment_is_pinned_to_a_version_and_delivered_read_only(env) -> None:
    files, store, sandbox = env
    await store.commit_blob(SCOPE, "data/sales.csv", b"q3,10\n")
    await store.commit_blob(SCOPE, "data/sales.csv", b"q4,12\n")  # version 2
    pinned = await files.attach(SID, SCOPE, "data/sales.csv", version=1, attached_by="u1")
    assert (pinned.version, pinned.sandbox_path) == (1, ".attachments/sales.csv")

    assert await files.deliver(SID, sandbox) == 1
    tree = tree_for_sandbox(sandbox)
    assert await tree.read_bytes(".attachments/sales.csv") == b"q3,10\n"  # the pinned version
    note = await files.context_note(SID)
    assert ".attachments/sales.csv (data/sales.csv, version 1)" in note and ".outputs/" in note

    # Attaching the same file again re-pins it; detaching removes it from the next delivery.
    again = await files.attach(SID, SCOPE, "data/sales.csv", version=None, attached_by="u1")
    assert again.version == 2 and [a.id for a in await files.attachments(SID)] == [again.id]
    assert await files.detach(SID, again.id)
    assert await files.deliver(SID, sandbox) == 0
    assert ".attachments/sales.csv" not in await tree.list_paths()


async def test_attaching_something_not_in_the_workspace_is_refused(env) -> None:
    files, _store, _sandbox = env
    with pytest.raises(FileNotInWorkspace):
        await files.attach(SID, SCOPE, "missing.txt", version=None, attached_by=None)


async def test_names_stay_unique_in_the_attachments_folder(env) -> None:
    files, store, _sandbox = env
    await store.commit_blob(SCOPE, "a/report.md", b"a")
    await store.commit_blob(SCOPE, "b/report.md", b"b")
    first = await files.attach(SID, SCOPE, "a/report.md", version=None, attached_by=None)
    second = await files.attach(SID, SCOPE, "b/report.md", version=None, attached_by=None)
    assert (first.name, second.name) == ("report.md", "report-2.md")


async def test_a_run_records_what_it_started_from(env) -> None:
    files, store, _sandbox = env
    await store.commit_blob(SCOPE, "notes.md", b"hello")
    await store.commit_blob(SCOPE, "data.csv", b"1,2")
    await files.attach(SID, SCOPE, "data.csv", version=None, attached_by=None)
    assert await files.snapshot(SID, "turn-1", SCOPE) == 3
    await store.commit_blob(SCOPE, "notes.md", b"changed later")  # does not change the record
    got = {(i.kind, i.path, i.version) for i in await files.run_inputs(SID, "turn-1")}
    assert got == {("attachment", "data.csv", 1), ("workspace", "data.csv", 1), ("workspace", "notes.md", 1)}


async def test_outputs_are_collected_once_and_saved_with_their_source(env) -> None:
    files, store, sandbox = env
    tree = tree_for_sandbox(sandbox)
    await tree.write_bytes(".outputs/report.md", b"# Q4\n")
    await tree.write_bytes(".outputs/charts/chart.png", b"\x89PNG")
    await tree.write_bytes("scratch.py", b"print(1)")  # not an output

    made = await files.collect(SID, "turn-1", sandbox)
    assert sorted(a.name for a in made) == ["charts/chart.png", "report.md"]
    assert {a.mime_type for a in made} == {"text/markdown", "image/png"}
    assert all(a.object_key.startswith(f"sessions/{SID}/artifacts/") for a in made)
    assert await files.collect(SID, "turn-2", sandbox) == []  # unchanged: nothing new
    await tree.write_bytes(".outputs/report.md", b"# Q4, revised\n")
    revised = await files.collect(SID, "turn-3", sandbox)
    assert [(a.name, a.turn_id) for a in revised] == [("report.md", "turn-3")]

    artifact, record = await files.save(SID, revised[0].id, scope_id=SCOPE, path="reports/q4.md", author="u1")
    assert (record.path, record.version) == ("reports/q4.md", 1)
    assert (artifact.saved_path, artifact.saved_version) == ("reports/q4.md", 1)
    (version,) = await store.list_versions(SCOPE, "reports/q4.md")
    assert version.source == f"artifact:{artifact.id}" and version.author == "u1"
    assert version.object_key.startswith(f"workspaces/{SCOPE}/objects/")  # its own copy
    _, data = await files.artifact_bytes(SID, artifact.id)
    assert data == b"# Q4, revised\n"


async def test_the_session_folders_never_reach_the_workspace(env) -> None:
    _files, store, sandbox = env
    raw = tree_for_sandbox(sandbox)
    await raw.write_bytes(".attachments/in.csv", b"x")
    await raw.write_bytes(".outputs/out.md", b"y")
    await raw.write_bytes("work.py", b"z")
    mirror = ScopeMirrorTree(raw)
    assert await mirror.list_paths() == ["work.py"]
    report = await store.sync(SCOPE, mirror, base={})
    assert [f.path for f in await store.list_files(SCOPE)] == ["work.py"] and list(report.written) == ["work.py"]
    await mirror.clear()  # a workspace refresh keeps the session's folders
    assert sorted(await raw.list_paths()) == [".attachments/in.csv", ".outputs/out.md"]
