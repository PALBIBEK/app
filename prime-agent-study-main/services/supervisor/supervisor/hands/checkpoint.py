"""Change-aware checkpoints of a session's sandbox, so a reset loses (almost) nothing.

The shape follows Crab (Semantics-Aware Checkpoint/Restore for agent sandboxes):

* **observe after each tool** that can change the workspace (``inspect``), and
  checkpoint only when something changed: most steps change nothing;
* **checkpoint at the turn boundary, off the critical path**: while the model
  works on its next step, and before the next tool runs, so a sandbox that dies
  during a tool loses at most that tool's effects;
* **files only, incrementally**: each changed file goes to the session's own
  scope in the workspace store (content-addressed objects), each removed one is
  deleted there; running processes are recorded, not saved, and a reset tells
  the model which ones to start again.

A restore loads that scope into the fresh sandbox.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Final

from supervisor.hands.inspect import REBUILT_DIRS, Changes, Fingerprint, fingerprint

if TYPE_CHECKING:
    from supervisor.sandbox.base import Sandbox
    from supervisor.seams.services.workspace_store import WorkspaceStore

logger = logging.getLogger(__name__)

__all__ = ["MAX_FILE_BYTES", "Checkpointer", "checkpoint_scope"]

MAX_FILE_BYTES: Final = 32 * 1024 * 1024
"""Larger files are left out of checkpoints (and named in the reset notice)."""

type Recorded = Callable[[str, "Sandbox", Changes, frozenset[str]], Awaitable[None]]
"""Journals a checkpoint: (session id, sandbox, what changed, processes running)."""


_SCOPES: Final = uuid.UUID("6f1c2b8e-4d0a-5b7e-9c3f-2a1d8e5b7c40")


def checkpoint_scope(session_id: str) -> str:
    """The workspace-store scope holding a session's checkpoints (owned by its owner).

    A UUID derived from the session id: the same on every worker, and the size
    the store's scope column takes.
    """
    return str(uuid.uuid5(_SCOPES, f"checkpoints:{session_id}"))


class Checkpointer:
    """Each session's last observation and last checkpoint, on this worker.

    State is per worker: after a takeover the first checkpoint saves every file
    again, which is correct, only slower.
    """

    __slots__ = ("_hand", "_locks", "_record", "_saved", "_seen", "_skipped", "_store")

    def __init__(self, store: WorkspaceStore, *, record: Recorded) -> None:
        self._store = store
        self._record = record
        self._seen: dict[str, Fingerprint] = {}
        self._saved: dict[str, Fingerprint] = {}
        self._hand: dict[str, str] = {}
        self._skipped: dict[str, tuple[str, ...]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def observe(self, session_id: str, sandbox: Sandbox, *, processes: bool) -> None:
        """Look at the sandbox after a tool that may have changed it."""
        self._seen[session_id] = await fingerprint(sandbox, processes=processes)
        self._hand[session_id] = sandbox.id

    def due(self, session_id: str) -> Changes | None:
        """What a checkpoint now would save, or None when nothing changed."""
        seen = self._seen.get(session_id)
        if seen is None:
            return None
        changes = Changes.between(self._saved.get(session_id), seen)
        return changes if changes.any else None

    async def checkpoint(self, session_id: str, sandbox: Sandbox) -> Changes | None:
        """Save what changed since the last checkpoint; None when nothing did."""
        async with self._locks.setdefault(session_id, asyncio.Lock()):
            if self._hand.get(session_id) != sandbox.id:
                return None  # the observation was of a sandbox that no longer serves
            changes = self.due(session_id)
            if changes is None:
                return None
            seen = self._seen[session_id]
            scope = checkpoint_scope(session_id)
            root = sandbox.info.workdir.rstrip("/") or "/workspace"
            saved = dict(seen.files)
            too_large: list[str] = []
            for path in changes.written:
                try:
                    data = await sandbox.read_file(f"{root}/{path}")
                except FileNotFoundError:
                    saved.pop(path, None)  # gone again since it was observed
                    continue
                if len(data) > MAX_FILE_BYTES:
                    too_large.append(path)
                    saved.pop(path, None)
                    continue
                await self._store.commit_blob(scope, path, data)
            for path in changes.removed:
                await self._store.soft_delete(scope, path)
            self._saved[session_id] = Fingerprint(files=saved, processes=seen.processes)
            self._skipped[session_id] = tuple(too_large)
            await self._record(session_id, sandbox, changes, seen.processes or frozenset())
            return changes

    async def restore(self, session_id: str, sandbox: Sandbox, *, processes: bool) -> int:
        """Load the session's last checkpoint into a fresh sandbox; returns the file count."""
        from supervisor.seams.services.session_workspace import tree_for_sandbox

        async with self._locks.setdefault(session_id, asyncio.Lock()):
            report = await self._store.hydrate(
                checkpoint_scope(session_id), tree_for_sandbox(sandbox)
            )
            # The restored files carry new times: that is the state saved, not a change.
            now = await fingerprint(sandbox, processes=processes)
            self._seen[session_id] = self._saved[session_id] = now
            self._hand[session_id] = sandbox.id
            return len(report.written)

    def processes(self, session_id: str) -> frozenset[str]:
        """What was running in the sandbox when it was last observed."""
        seen = self._seen.get(session_id) or self._saved.get(session_id)
        return frozenset(seen.processes or ()) if seen is not None else frozenset()

    def notice(self, session_id: str) -> str:
        """What a reset will not bring back, for the model."""
        lost = sorted(self._saved.get(session_id, Fingerprint()).processes or ())
        parts = [
            (
                "your files are restored as they were at the last checkpoint, taken after "
                "every step that changed them"
            ),
            (
                f"folders that are rebuilt ({', '.join(REBUILT_DIRS[:3])}, caches) are not: "
                "reinstall or rebuild them"
            ),
        ]
        if lost:
            shown = "; ".join(cmd[:120] for cmd in lost[:8])
            parts.append(f"these processes were running and must be started again: {shown}")
        if skipped := self._skipped.get(session_id):
            parts.append(f"files too large to save: {', '.join(skipped[:8])}")
        return "; ".join(parts) + "."

    def forget(self, session_id: str) -> None:
        for state in (self._seen, self._saved, self._hand, self._skipped, self._locks):
            state.pop(session_id, None)
