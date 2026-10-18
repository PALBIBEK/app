"""Markdown-backed memory storage — the source of truth (port of `storage.rs`).

Layout, copied from Grok's `lib.rs`:

    ~/.githi/memory/
      ├── MEMORY.md                             # global, cross-project
      └── {slug}-{hash8}/                       # per-workspace
          ├── MEMORY.md                         # curated project knowledge
          └── sessions/
              └── YYYY-MM-DD-{slug}-{run8}.md   # raw run logs

Workspace memory is keyed by a hash of the workspace path and stored under the
user's home, **never inside the repository**. Two reasons, both load-bearing:
a `.githi/` directory inside a project shows up in `git status` and
eventually in someone's commit, and the same project checked out at two paths
would otherwise share (and corrupt) one memory.
"""

from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

SCAFFOLD_MAX_LEN = 500
"""Below this many characters, a file *may* be an empty stub -- see
`is_scaffold_template` for why length alone is not enough."""

_SCAFFOLD_MARKERS = (
    "Auto-populated by dream consolidation",
    "Add project-specific knowledge here",
    "Add any cross-project preferences here",
)


class MemoryScope(str, Enum):
    GLOBAL = "global"
    WORKSPACE = "workspace"


def _slugify(text: str, limit: int = 40) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return slug[:limit] or "workspace"


def workspace_key(workspace: Path) -> str:
    """`{slug}-{hash8}` for a workspace path.

    The slug keeps the directory identifiable when a human lists
    `~/.githi/memory`; the hash keeps it unique. Both halves are needed:
    `~/a/proj` and `~/b/proj` produce the same slug, and only the hash
    (taken over the *resolved absolute* path) separates them.
    """
    resolved = Path(workspace).expanduser().resolve()
    digest = hashlib.blake2b(str(resolved).encode("utf-8"), digest_size=8).hexdigest()[:8]
    return f"{_slugify(resolved.name)}-{digest}"


class MemoryStorage:
    def __init__(self, home: Path | None = None, workspace: Path | None = None) -> None:
        self.home = Path(home) if home else Path(
            os.environ.get("GITHI_MEMORY_HOME", Path.home() / ".githi" / "memory")
        )
        self.workspace = Path(workspace) if workspace else Path.cwd()

    # ---------------- layout ----------------

    def workspace_dir(self) -> Path:
        return self.home / workspace_key(self.workspace)

    def global_memory_path(self) -> Path:
        return self.home / "MEMORY.md"

    def workspace_memory_path(self) -> Path:
        return self.workspace_dir() / "MEMORY.md"

    def sessions_dir(self) -> Path:
        return self.workspace_dir() / "sessions"

    def path_for(self, scope: MemoryScope) -> Path:
        return (self.global_memory_path() if scope is MemoryScope.GLOBAL
                else self.workspace_memory_path())

    # ---------------- curated memory ----------------

    def read(self, scope: MemoryScope) -> str:
        path = self.path_for(scope)
        if not path.is_file():
            return ""                    # absent memory is empty, not an error
        return path.read_text(encoding="utf-8", errors="replace")

    def write(self, scope: MemoryScope, content: str) -> Path:
        """Write via a temp file and `os.replace`.

        `MEMORY.md` *is* the knowledge — there is no other copy to recover
        from — so a crash midway through a direct write would silently
        truncate it. `os.replace` is atomic on POSIX and Windows, so a reader
        sees either the old file or the new one, never a half-written one.
        """
        path = self.path_for(scope)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)
        return path

    # ---------------- session logs ----------------

    def session_path(self, slug: str, run_id: str,
                     when: datetime | None = None) -> Path:
        stamp = (when or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
        return self.sessions_dir() / f"{stamp}-{_slugify(slug)}-{run_id[:8]}.md"

    def append_session(self, slug: str, content: str, run_id: str = "",
                       when: datetime | None = None) -> Path:
        """Append to this run's session log, creating it if needed.

        Append rather than rewrite so a run that dies partway still leaves
        everything it had already recorded — the same reasoning as an
        append-only transcript.
        """
        run = run_id or datetime.now(timezone.utc).strftime("%H%M%S%f")[:8]
        path = self.session_path(slug, run, when)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(content)
        return path

    def read_session(self, path: Path) -> str:
        if not Path(path).is_file():
            return ""
        return Path(path).read_text(encoding="utf-8", errors="replace")

    def list_sessions(self) -> list[Path]:
        directory = self.sessions_dir()
        if not directory.is_dir():
            return []
        return sorted(p for p in directory.glob("*.md") if p.is_file())

    def delete_session(self, path: Path) -> None:
        Path(path).unlink(missing_ok=True)


def is_scaffold_template(content: str) -> bool:
    """Is this an auto-generated stub rather than real knowledge?

    **Both** conditions are required: short *and* carrying a marker. Grok is
    explicit that length alone is wrong, and the reason is worth keeping in
    mind — a genuine memory that happens to quote a marker phrase ("the stub
    said 'Add project-specific knowledge here'") would otherwise be
    classified as empty, and consolidation would treat a populated
    `MEMORY.md` as though there were no prior knowledge to merge, discarding
    it.
    """
    trimmed = content.strip()
    if not trimmed:
        return True
    return len(trimmed) < SCAFFOLD_MAX_LEN and any(m in trimmed for m in _SCAFFOLD_MARKERS)
