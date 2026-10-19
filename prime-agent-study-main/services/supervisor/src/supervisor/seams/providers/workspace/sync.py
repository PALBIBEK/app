"""Three-way sync of a session's workspace tree back to its scope (F20).

A session works on a copy of the scope taken when it hydrated: the *base*.
Comparing its tree with the scope's current files instead (as sync once did)
cannot tell "this session changed the file" from "someone else changed it
since", so a session that hydrated earlier silently overwrote other sessions'
writes and deleted the files they had created since.

Against the base, every path is one of:

- unchanged by the session: left alone, whatever the scope holds now;
- changed or created by the session: committed only if the scope still holds
  the version the session started from (compare-and-set), else a conflict;
- deleted by the session: deleted only if the scope still holds the version
  the session saw, else a conflict;
- new in the scope since the session hydrated: not the session's business.

Without a base (an unknown starting point) nothing is deleted and nothing the
scope holds is overwritten: every write is a create, and a differing file is
a conflict. Callers resolve conflicts (the session manager: durable wins and
the sandbox is rehydrated), never last-writer-wins.
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable, Mapping

from supervisor.seams.services.workspace_store import (
    CasConflict,
    WorkspaceBase,
    WorkspaceFileRecord,
    WorkspaceStore,
    WorkspaceSyncReport,
    WorkspaceTreeIO,
)

__all__ = ["base_of", "three_way_sync"]

BeforeCas = Callable[[str, str], Awaitable[None]]


def base_of(records: list[WorkspaceFileRecord]) -> WorkspaceBase:
    """The base a tree has once it holds exactly ``records``."""
    return {r.path: (r.version, r.content_sha256) for r in records}


async def three_way_sync(
    store: WorkspaceStore,
    scope_id: str,
    tree: WorkspaceTreeIO,
    base: Mapping[str, tuple[int, str]] | None,
    *,
    before_cas: BeforeCas | None = None,
) -> WorkspaceSyncReport:
    """Commit what the session changed since ``base``; report the new base."""
    seen = dict(base or {})
    durable = {r.path: r for r in await store.list_files(scope_id)}
    written: list[str] = []
    removed: list[str] = []
    conflicts: list[str] = []
    bytes_n = 0
    tree_paths = set(await tree.list_paths())

    for path in sorted(tree_paths):
        data = await tree.read_bytes(path)
        digest = hashlib.sha256(data).hexdigest()
        started = seen.get(path)
        now = durable.get(path)
        if started is not None and started[1] == digest:
            continue  # the session did not touch it
        if now is not None and now.content_sha256 == digest:
            seen[path] = (now.version, digest)  # already there (the same edit made twice)
            continue
        if before_cas is not None:
            await before_cas(scope_id, path)
        try:
            committed = await store.commit_blob(
                scope_id, path, data, expected_version=started[0] if started else 0
            )
        except CasConflict:
            conflicts.append(path)
            continue
        seen[path] = (committed.version, digest)
        written.append(path)
        bytes_n += len(data)

    for path in sorted(set(seen) - tree_paths):
        version, digest = seen.pop(path)
        now = durable.get(path)
        if now is None:
            continue  # gone from the scope too
        if (now.version, now.content_sha256) != (version, digest):
            conflicts.append(path)  # changed by someone else since; the session deleted it
            seen[path] = (version, digest)
            continue
        try:
            await store.soft_delete(scope_id, path, expected_version=version)
        except CasConflict:  # changed between the listing and now
            conflicts.append(path)
            seen[path] = (version, digest)
            continue
        removed.append(path)

    return WorkspaceSyncReport(
        scope_id=scope_id,
        written=tuple(written),
        removed=tuple(removed),
        conflicts=tuple(conflicts),
        bytes_transferred=bytes_n,
        base=seen,
    )
