"""Export memory as a portable tarball (port of Grok's `archive.rs`).

Grok builds `memory.tar.gz` at session finalize and uploads it so a replay
environment can be reconstructed with full fidelity. The same artifact serves
the mundane cases: backup, moving memory to another machine, or handing
someone a reproducible snapshot of what the agent knew.

**Markdown only — the index is excluded.** It is derivable from the files, so
including it would bloat the archive and, worse, risk restoring a stale
database over fresh markdown. Anything that unpacks this can rebuild the
index by scanning.
"""

from __future__ import annotations

import tarfile
from pathlib import Path

from .storage import MemoryStorage


def build_archive(storage: MemoryStorage, output: Path) -> Path:
    """Write `output` containing every markdown file in the memory store.

    Paths inside the archive are relative to the memory home, so unpacking
    into a fresh `~/.githi/memory` reproduces the original layout — including
    the `{slug}-{hash8}` workspace directory, which is what makes a restored
    archive resolve to the same workspace.
    """
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)

    members = [
        path for path in (
            storage.global_memory_path(),
            storage.workspace_memory_path(),
            *storage.list_sessions(),
        )
        if path.is_file()
    ]

    with tarfile.open(output, "w:gz") as tar:
        for path in members:
            try:
                arcname = str(path.relative_to(storage.home))
            except ValueError:
                arcname = path.name
            tar.add(path, arcname=arcname)

    # An empty archive is still a valid archive: a store with no memory yet
    # is a normal state, and failing here would make the first backup an
    # error rather than a no-op.
    return output
