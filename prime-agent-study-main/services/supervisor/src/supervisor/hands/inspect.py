"""What changed in a sandbox: the cheap observation behind change-aware checkpoints.

Crab (Semantics-Aware Checkpoint/Restore for agent sandboxes) checkpoints only
the turns whose OS-visible state changed, observed with eBPF. eBPF needs host
privileges that a hosted sandbox (E2B) never grants, so this observes the same
two things portably, with one shell command in the sandbox:

* **files**: every file's size and modification time (POSIX ``find`` + ``stat``,
  busybox included), skipping folders that are rebuilt rather than saved;
* **processes** (isolating providers only): every command line running there,
  read from ``/proc`` with shell builtins, minus the listing's own processes.

A file edited twice within one second to the same size looks unchanged (the
modification time has one-second resolution in busybox); everything else that
changes a file or starts or stops a process is seen.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from supervisor.sandbox.base import Sandbox

__all__ = ["REBUILT_DIRS", "Changes", "Fingerprint", "fingerprint"]

REBUILT_DIRS: Final = (
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".cache",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
)
"""Folders an install or a build recreates; never checkpointed (the model is told)."""

_PROCS: Final = "\x1e"
"""Separates the file listing from the process listing in the script's output."""

_SCRIPT: Final = (
    "find . \\( "
    + " -o ".join(f"-name {name}" for name in REBUILT_DIRS)
    + " \\) -prune -o -type f -exec stat -c '%s %Y %n' {} + 2>/dev/null\n"
    'printf "\\036\\n"\n'
    '[ "$1" = processes ] || exit 0\n'
    'echo "self $$"\n'
    "for d in /proc/[0-9]*; do\n"
    '  read -r line < "$d/stat" 2>/dev/null || continue\n'
    "  rest=${line##*) }\n"
    "  set -- $rest\n"
    '  printf \'p %s %s\\t\' "${d#/proc/}" "$2"\n'
    '  cat "$d/cmdline" 2>/dev/null\n'
    "  printf '\\n'\n"
    "done\n"
)


@dataclass(frozen=True, slots=True)
class Fingerprint:
    files: Mapping[str, tuple[int, int]] = field(default_factory=dict)
    """Workspace-relative path → (size in bytes, modification time in seconds)."""
    processes: frozenset[str] | None = None
    """Command lines running in the sandbox; None where they cannot be observed."""


@dataclass(frozen=True, slots=True)
class Changes:
    written: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    processes_changed: bool = False

    @property
    def files_changed(self) -> bool:
        return bool(self.written or self.removed)

    @property
    def any(self) -> bool:
        return self.files_changed or self.processes_changed

    @classmethod
    def between(cls, before: Fingerprint | None, after: Fingerprint) -> Changes:
        """What ``after`` changed since ``before`` (everything, when nothing came before)."""
        old = before.files if before is not None else {}
        written = tuple(sorted(p for p, stat in after.files.items() if old.get(p) != stat))
        removed = tuple(sorted(p for p in old if p not in after.files))
        procs_before = before.processes if before is not None else None
        return cls(
            written=written,
            removed=removed,
            processes_changed=after.processes is not None and after.processes != procs_before,
        )


async def fingerprint(sandbox: Sandbox, *, processes: bool) -> Fingerprint:
    """Observe the sandbox's files (and its processes, when ``processes``).

    The listing runs in the sandbox's workspace root, as every workspace command
    does, and reports paths relative to it.
    """
    done = await sandbox.exec(["sh", "-c", _SCRIPT, "sh", "processes" if processes else "files"])
    files_part, _, procs_part = done.stdout_text.partition(f"{_PROCS}\n")
    return Fingerprint(
        files=_files(files_part),
        processes=_processes(procs_part) if processes else None,
    )


def _files(listing: str) -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    for line in listing.splitlines():
        size, _, rest = line.partition(" ")
        mtime, _, path = rest.partition(" ")
        if not (size.isdigit() and mtime.isdigit() and path.startswith("./")):
            continue
        out[path[2:]] = (int(size), int(mtime))
    return out


def _processes(listing: str) -> frozenset[str]:
    """Command lines, minus the listing shell and its own children."""
    lines = listing.splitlines()
    self_pid = lines[0].split()[1] if lines and lines[0].startswith("self ") else ""
    running: set[str] = set()
    for line in lines[1:]:
        head, _, cmdline = line.partition("\t")
        parts = head.split()
        if len(parts) != 3 or parts[0] != "p":
            continue
        pid, ppid = parts[1], parts[2]
        command = " ".join(arg for arg in cmdline.split("\x00") if arg)
        if not command or self_pid in (pid, ppid):
            continue  # kernel threads, and the listing itself
        running.add(command)
    return frozenset(running)
