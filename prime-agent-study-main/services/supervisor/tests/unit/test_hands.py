"""Hands: sandboxes built on first need, replaced after a loss, checkpointed by change.

Real local sandboxes and the in-memory workspace store; no mocks of the code under test.
"""

from __future__ import annotations

import asyncio
import shutil
import uuid
from pathlib import Path

import pytest

from supervisor.hands import HandRecipe, HandWorkspace, SessionHands
from supervisor.hands.checkpoint import Checkpointer, checkpoint_scope
from supervisor.hands.inspect import Changes, Fingerprint, fingerprint
from supervisor.sandbox.base import Sandbox
from supervisor.sandbox.providers.local_process import LocalProcessSandbox
from supervisor.seams.providers.storage.memory_object_storage import MemoryObjectStorage
from supervisor.seams.providers.workspace.memory_workspace_store import MemoryWorkspaceStore
from supervisor.tools import FileMissing, PathEscape, WorkspaceReset, WorkspaceUnavailable


class Builds:
    """A recipe's provider: counts builds, hands out real local sandboxes."""

    name = "local_process"

    def __init__(self, base: Path) -> None:
        self.base = base
        self.built: list[LocalProcessSandbox] = []
        self.survivor: Sandbox | None = None

    async def create(self, **_: object) -> LocalProcessSandbox:
        await asyncio.sleep(0.01)  # concurrent callers overlap here
        root = self.base / uuid.uuid4().hex
        root.mkdir()
        sandbox = LocalProcessSandbox(root, f"sbx-{len(self.built)}")
        self.built.append(sandbox)
        return sandbox

    async def adopt(self, session_id: str) -> Sandbox | None:
        return self.survivor


class Hooks:
    def __init__(self, *, fail_hydrate: bool = False) -> None:
        self.events: list[tuple[str, str, str]] = []
        self.fail_hydrate = fail_hydrate

    async def hydrate(self, session_id: str, sandbox: Sandbox, *, adopted: bool = False) -> None:
        if self.fail_hydrate:
            raise RuntimeError("store unreachable")

    async def provisioned(self, session_id: str, sandbox: Sandbox, cause: str) -> None:
        self.events.append(("up", sandbox.id, cause))

    async def lost(self, session_id: str, sandbox: Sandbox, reason: str, logs: bytes | None) -> None:
        self.events.append(("lost", sandbox.id, reason))
        self.logs = logs


def _hands(tmp_path: Path, hooks: Hooks | None = None) -> tuple[SessionHands, Builds, Hooks]:
    provider = Builds(tmp_path)
    hooks = hooks or Hooks()
    hands = SessionHands(hooks)
    hands.register("s", HandRecipe(provider=provider, image=None))  # type: ignore[arg-type]
    return hands, provider, hooks


async def test_a_sandbox_is_built_once_on_first_need(tmp_path: Path) -> None:
    hands, provider, hooks = _hands(tmp_path)
    assert provider.built == [] and hands.sandbox("s") is None, "nothing at registration"
    first, second = await asyncio.gather(hands.ensure("s"), hands.ensure("s"))
    assert first is second and len(provider.built) == 1, "concurrent callers share one build"
    assert hooks.events == [("up", "sbx-0", "first_use")]
    hands.register("none", None)
    assert await hands.ensure("none") is None, "a harness without a sandbox gets none"


async def test_a_lost_sandbox_is_replaced_by_the_next_need(tmp_path: Path) -> None:
    hands, _, hooks = _hands(tmp_path)
    old = await hands.ensure("s")
    assert old is not None
    assert not await hands.lose("s", "not-the-current-one", "x"), "a stale loss changes nothing"
    assert await hands.lose("s", old.id, "OOM killed")
    assert hands.sandbox("s") is None and not await old.is_alive(), "the dead one is let go"
    new = await hands.ensure("s")
    assert new is not None and new.id != old.id
    assert hooks.events[1:] == [("lost", old.id, "OOM killed"), ("up", new.id, "replaced")]


class Recorded(LocalProcessSandbox):
    """A sandbox whose provider keeps logs, which go with it when it is destroyed."""

    async def logs(self, *, limit_bytes: int = 256_000) -> bytes | None:
        return None if self._destroyed else b"exit_code=137 oom_killed=True\n"


async def test_a_lost_sandboxs_logs_are_read_before_it_is_destroyed(tmp_path: Path) -> None:
    hands, provider, hooks = _hands(tmp_path)
    (tmp_path / "r").mkdir()
    provider.built.append(sandbox := Recorded(tmp_path / "r", "sbx-logs"))
    hands.attach("s", sandbox)
    assert await hands.lose("s", sandbox.id, "OOM killed")
    assert hooks.logs == b"exit_code=137 oom_killed=True\n"


async def test_a_sandbox_without_its_files_never_serves(tmp_path: Path) -> None:
    hands, provider, _ = _hands(tmp_path, Hooks(fail_hydrate=True))
    with pytest.raises(RuntimeError, match="store unreachable"):
        await hands.ensure("s")
    assert hands.sandbox("s") is None
    assert not await provider.built[0].is_alive(), "the half-made sandbox is destroyed"


async def test_a_resume_adopts_the_survivor(tmp_path: Path) -> None:
    hands, provider, hooks = _hands(tmp_path)
    root = tmp_path / "left"
    root.mkdir()
    provider.survivor = LocalProcessSandbox(root, "left-behind")
    assert await hands.adopt("s") is provider.survivor
    assert provider.built == [] and hooks.events == [("up", "left-behind", "adopted")]


async def test_a_dead_sandbox_becomes_a_workspace_reset(tmp_path: Path) -> None:
    hands, _, _ = _hands(tmp_path)
    workspace = HandWorkspace(lambda: hands.sandbox("s"))
    with pytest.raises(WorkspaceUnavailable):
        await workspace.read_bytes("a.txt")
    sandbox = await hands.ensure("s")
    assert isinstance(sandbox, LocalProcessSandbox)
    await workspace.write_bytes("a.txt", b"hi")
    with pytest.raises(FileMissing):
        await workspace.read_bytes("missing.txt")  # alive: the tool's own error
    with pytest.raises(PathEscape):
        await workspace.read_bytes("../etc/passwd")
    shutil.rmtree(sandbox.root)  # the sandbox dies under the session
    with pytest.raises(WorkspaceReset) as reset:
        await workspace.read_bytes("a.txt")
    assert reset.value.code == "WORKSPACE_RESET" and "fresh sandbox" in str(reset.value)
    with pytest.raises(WorkspaceReset):
        await workspace.run("ls", timeout_s=5)


async def _sandbox(tmp_path: Path, name: str) -> LocalProcessSandbox:
    root = tmp_path / name
    root.mkdir()
    return LocalProcessSandbox(root, name)


async def test_the_fingerprint_sees_file_changes_and_skips_rebuilt_folders(
    tmp_path: Path,
) -> None:
    sandbox = await _sandbox(tmp_path, "fp")
    await sandbox.write_file("/workspace/src/main.py", b"print(1)")
    await sandbox.write_file("/workspace/node_modules/x/index.js", b"x")
    await sandbox.write_file("/workspace/a file.txt", b"spaces")
    first = await fingerprint(sandbox, processes=False)
    assert set(first.files) == {"src/main.py", "a file.txt"}
    assert first.processes is None
    assert Changes.between(first, first) == Changes()
    await sandbox.write_file("/workspace/src/main.py", b"print(1234)")
    await sandbox.exec(["rm", "a file.txt"])
    second = await fingerprint(sandbox, processes=False)
    changes = Changes.between(first, second)
    assert (changes.written, changes.removed) == (("src/main.py",), ("a file.txt",))
    assert Changes.between(None, second).written == ("src/main.py",), "nothing saved yet: all"


def test_process_changes_are_seen() -> None:
    before = Fingerprint(files={}, processes=frozenset({"python app.py"}))
    after = Fingerprint(files={}, processes=frozenset({"python app.py", "npm run dev"}))
    assert Changes.between(before, after).processes_changed
    assert not Changes.between(before, before).any


async def test_checkpoints_save_only_changes_and_restore_into_a_fresh_sandbox(
    tmp_path: Path,
) -> None:
    store = MemoryWorkspaceStore(objects=MemoryObjectStorage())
    recorded: list[Changes] = []

    async def record(sid: str, sandbox: Sandbox, changes: Changes, procs: frozenset[str]) -> None:
        recorded.append(changes)

    checkpoints = Checkpointer(store, record=record)
    old = await _sandbox(tmp_path, "old")
    await old.write_file("/workspace/notes.txt", b"v1")
    await old.write_file("/workspace/lib/util.py", b"x = 1")
    await checkpoints.observe("s", old, processes=False)
    assert (await checkpoints.checkpoint("s", old)).written == ("lib/util.py", "notes.txt")
    assert await checkpoints.checkpoint("s", old) is None, "nothing changed since"

    await old.write_file("/workspace/notes.txt", b"version 2")
    await old.exec(["rm", "lib/util.py"])
    await checkpoints.observe("s", old, processes=False)
    second = await checkpoints.checkpoint("s", old)
    assert second is not None
    assert (second.written, second.removed) == (("notes.txt",), ("lib/util.py",))
    stored = {f.path for f in await store.list_files(checkpoint_scope("s"))}
    assert stored == {"notes.txt"}

    fresh = await _sandbox(tmp_path, "fresh")
    assert await checkpoints.restore("s", fresh, processes=False) == 1
    assert await fresh.read_file("/workspace/notes.txt") == b"version 2"
    assert checkpoints.due("s") is None, "a restore is not a change to save again"
    assert len(recorded) == 2
    assert "restored" in checkpoints.notice("s") and "node_modules" in checkpoints.notice("s")


async def test_an_observation_of_a_replaced_sandbox_is_not_checkpointed(tmp_path: Path) -> None:
    checkpoints = Checkpointer(MemoryWorkspaceStore(objects=MemoryObjectStorage()), record=_none)
    old = await _sandbox(tmp_path, "gone")
    await old.write_file("/workspace/a", b"1")
    await checkpoints.observe("s", old, processes=False)
    replacement = await _sandbox(tmp_path, "new")
    assert await checkpoints.checkpoint("s", replacement) is None


async def _none(*_: object) -> None:
    return None


async def test_an_adopted_sandbox_keeps_its_newer_files() -> None:
    """A tool can end after the last checkpoint and before its worker dies: the sandbox
    left behind holds that change, so adopting it must not restore the checkpoint."""
    from types import SimpleNamespace

    from supervisor.session.manager_base import _HandHooks

    calls: list[str] = []

    class Checkpoints:
        async def observe(self, key: str, sandbox: object, *, processes: bool) -> None:
            calls.append("observe")

        async def restore(self, key: str, sandbox: object, *, processes: bool) -> int:
            calls.append("restore")
            return 0

    async def has_checkpoint(key: str) -> bool:
        return True

    manager = SimpleNamespace(
        _workspace={}, _checkpoints=Checkpoints(), _has_checkpoint=has_checkpoint,
        _hand_isolated=lambda key: False, files=None,
    )
    hooks = _HandHooks(manager)  # type: ignore[arg-type]
    await hooks.hydrate("s", object(), adopted=True)  # type: ignore[arg-type]
    assert calls == ["observe"]
    await hooks.hydrate("s", object())  # type: ignore[arg-type]
    assert calls == ["observe", "restore"]  # a fresh sandbox does load the checkpoint


async def test_a_host_sandbox_path_names_the_same_file(tmp_path: Path) -> None:
    """``pwd`` in a host-process sandbox shows its own directory; a path under it is
    the same file as the workspace-relative one, not an escape."""
    root = tmp_path / "box"
    root.mkdir()
    sandbox = LocalProcessSandbox(root, "box")
    workspace = HandWorkspace(lambda: sandbox)
    assert workspace.relpath(f"{root}/cli.py") == "cli.py"
    assert workspace.relpath("/workspace/cli.py") == "cli.py"
    await workspace.write_bytes(f"{root}/cli.py", b"print(1)\n")
    assert (root / "cli.py").read_text() == "print(1)\n"
