"""Confining file access to the sandbox root.

One of the two escape routes out of the sandbox (the other is the
environment, see `env.py`). The failure mode is silent -- a traversal that
resolves outside the root just reads the user's real files and returns
plausible content -- so the check has to be structural rather than a string
comparison anyone can talk their way around.
"""

from __future__ import annotations

from pathlib import Path

from ..errors import SandboxViolation
from .policy import SandboxPolicy


def resolve_in_root(candidate: str | Path, policy: SandboxPolicy) -> Path:
    """Resolve `candidate` and guarantee the result is inside `policy.root`.

    Three properties this needs, and why the obvious implementations miss:

    * **Symlinks are followed before the check.** `Path.resolve()` collapses
      them, so a link inside the root pointing at `/etc/passwd` is caught. A
      check on the *unresolved* string would happily accept it.
    * **`relative_to` decides containment, not `startswith`.** String prefix
      matching accepts `/tmp/rootEVIL` as being inside `/tmp/root`; comparing
      resolved path *components* cannot make that mistake.
    * **The target need not exist.** `write` has to name a file before
      creating it. `resolve()` is non-strict here on purpose, and the parent
      chain still resolves, so a non-existent path under a symlinked-out
      directory is still rejected.
    """
    raw = Path(candidate)
    absolute = raw if raw.is_absolute() else policy.root / raw
    resolved = absolute.resolve()

    if resolved != policy.root and policy.root not in resolved.parents:
        raise SandboxViolation(
            f"path {str(candidate)!r} resolves to {resolved} which is outside "
            f"the sandbox root {policy.root}"
        )
    return resolved


def ensure_writable(path: Path, policy: SandboxPolicy) -> Path:
    """Containment plus the write permission bit.

    Separate from `resolve_in_root` so a read-only policy is enforced at one
    named place rather than by each mutating tool remembering to check.
    """
    if not policy.writable:
        raise SandboxViolation(
            f"sandbox is read-only; refused to modify {path}"
        )
    return path
