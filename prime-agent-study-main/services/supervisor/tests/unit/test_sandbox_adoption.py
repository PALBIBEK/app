"""A session's sandbox outlives its worker: the adopting worker takes it over (F21).

A session without a scope keeps its files only in its sandbox, so a worker
that died used to take them with it: the adopting worker started an empty one.
"""

from __future__ import annotations

from pathlib import Path

from supervisor.sandbox.providers.local_process import LocalProcessProvider

SESSION = {"supervisor.session_id": "s1"}


async def test_another_worker_on_the_host_adopts_the_sessions_sandbox(tmp_path: Path) -> None:
    dead = LocalProcessProvider(base_dir=tmp_path)
    sandbox = await dead.create(labels=SESSION)
    await sandbox.write_file("/workspace/work.txt", b"unsaved work")

    adopting = LocalProcessProvider(base_dir=tmp_path)  # a new process: nothing in memory
    adopted = await adopting.adopt("s1")

    assert adopted is not None and adopted.info.id == sandbox.info.id
    assert await adopted.read_file("/workspace/work.txt") == b"unsaved work"
    assert await adopting.adopt("someone-else") is None


async def test_a_destroyed_sandbox_cannot_be_adopted(tmp_path: Path) -> None:
    provider = LocalProcessProvider(base_dir=tmp_path)
    sandbox = await provider.create(labels=SESSION)
    await sandbox.destroy()
    assert await LocalProcessProvider(base_dir=tmp_path).adopt("s1") is None


async def test_closing_a_worker_keeps_session_sandboxes_and_removes_the_rest(tmp_path: Path) -> None:
    provider = LocalProcessProvider(base_dir=tmp_path)
    kept = await provider.create(labels=SESSION)
    scratch = await provider.create()
    await provider.close()
    assert Path(kept.info.labels["host_root"]).is_dir()
    assert not Path(scratch.info.labels["host_root"]).exists()
    assert await LocalProcessProvider(base_dir=tmp_path).adopt("s1") is not None


async def test_the_labels_are_outside_the_workspace(tmp_path: Path) -> None:
    """They name the session a directory belongs to, so file tools must not reach them.
    (local_process has no isolation: its shell can; strict deployments refuse it.)"""
    sandbox = await LocalProcessProvider(base_dir=tmp_path).create(labels=SESSION)
    assert await sandbox.exists("/workspace/../.labels") is False
    assert list(Path(sandbox.info.labels["host_root"]).iterdir()) == []


async def test_another_process_finds_the_sandbox_serving_a_session_and_leaves_it_to_its_worker(tmp_path: Path) -> None:
    """The API shows a person the browser in the worker's sandbox: it finds the sandbox, acts in
    it, and owns nothing, so closing the API leaves the worker's sandbox as it was."""
    worker = LocalProcessProvider(base_dir=tmp_path)
    sandbox = await worker.create(labels=SESSION)
    await sandbox.write_file("/workspace/page.txt", b"the agent's page")

    api = LocalProcessProvider(base_dir=tmp_path)
    found = await api.serving("s1")
    await api.close()

    assert found is not None and found.info.id == sandbox.info.id
    assert await found.read_file("/workspace/page.txt") == b"the agent's page"
    assert await sandbox.read_file("/workspace/page.txt") == b"the agent's page", "closing the API destroyed it"
    assert await api.serving("someone-else") is None
    await sandbox.destroy()
    assert await api.serving("s1") is None
