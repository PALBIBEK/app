"""Coordination for the consolidation pass (port of Grok's `dream_lock.rs`).

A PID lock file that doubles as the "last consolidated at" record — its mtime
*is* the timestamp, so there is no second file to keep in sync.

The property worth stating: **the lock reclaims itself.** A crashed run
leaves the file behind, and if a dead holder could block consolidation
forever, memory would silently stop improving with nothing reporting it. Two
independent escape hatches — the holding process is gone, or the lock is
older than the staleness window — because either check alone has a failure
mode (a recycled PID looks alive; a long legitimate run looks stale).
"""

from __future__ import annotations

import os
import time
from pathlib import Path

LOCK_FILE_NAME = ".dream-lock"
DEFAULT_STALE_SECONDS = 3600


def _is_process_alive(pid: int) -> bool:
    """Signal 0 probes for existence without delivering anything.

    `PermissionError` counts as alive: the process exists, it just belongs to
    another user — treating that as dead would let two dreams run at once.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class DreamLock:
    def __init__(self, directory: Path,
                 stale_seconds: int = DEFAULT_STALE_SECONDS) -> None:
        self.path = Path(directory) / LOCK_FILE_NAME
        self.stale_seconds = stale_seconds

    def last_consolidated_at(self) -> float | None:
        """Unix time of the last completed consolidation, or None if never.

        The lock file's mtime carries this, so a completed run leaves exactly
        one artifact rather than a lock plus a state file that can disagree.
        """
        try:
            return self.path.stat().st_mtime
        except OSError:
            return None

    def try_acquire(self) -> bool:
        if self.path.exists():
            if not self._is_reclaimable():
                return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(str(os.getpid()), encoding="utf-8")
        return True

    def _is_reclaimable(self) -> bool:
        try:
            raw = self.path.read_text(encoding="utf-8").strip()
            age = time.time() - self.path.stat().st_mtime
        except OSError:
            return True

        # Empty content means the file is a *timestamp*, not a held mutex:
        # a previous run finished and left its mtime behind for the time
        # gate. The two roles share one file, and this is what separates
        # them -- without it, a completed run's own lock blocks every
        # future run forever.
        if not raw:
            return True

        if age >= self.stale_seconds:
            return True                      # too old to be a live run
        try:
            pid = int(raw)
        except ValueError:
            # A truncated or garbage lock file is the signature of a crash
            # mid-write; refusing to reclaim it would wedge consolidation
            # permanently on a file nobody owns.
            return True
        if pid == os.getpid():
            return False
        return not _is_process_alive(pid)

    def release(self) -> None:
        """Give up the mutex while preserving the consolidation timestamp.

        Deleting the file would work for the mutex but would throw away the
        mtime the time gate depends on, so the next run would consolidate
        immediately regardless of `min_hours`. Instead the holder is cleared
        and the mtime restored.
        """
        if not self.path.exists():
            return
        try:
            mtime = self.path.stat().st_mtime
            self.path.write_text("", encoding="utf-8")
            os.utime(self.path, (mtime, mtime))
        except OSError:
            pass

    def mark_consolidated(self) -> None:
        """Record a completed consolidation by touching the lock file.

        Deliberately leaves the file in place: its mtime is what the time
        gate reads on the next run.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Empty holder + fresh mtime: the run is done (so others may acquire)
        # and this is when it finished (so the time gate can read it).
        self.path.write_text("", encoding="utf-8")
        now = time.time()
        os.utime(self.path, (now, now))


def sessions_since(sessions_dir: Path, cutoff: float) -> list[str]:
    """Stems of session files modified after `cutoff`.

    Modification time rather than the date in the filename: a session started
    yesterday and appended to this morning is new work, and the filename
    would call it old.
    """
    directory = Path(sessions_dir)
    if not directory.is_dir():
        return []
    stems: list[str] = []
    for path in sorted(directory.glob("*.md")):
        try:
            if path.stat().st_mtime > cutoff:
                stems.append(path.stem)
        except OSError:
            continue
    return stems
