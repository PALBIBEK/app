""".gitignore rules, as ripgrep and fd apply them for pi's grep and find.

pi's search tools run ``rg`` and ``fd``, which skip what the project's ``.gitignore``
files exclude (``node_modules/``, build output, …), so a search returns the project's
own files rather than thousands of vendored ones. githi's tools search the workspace
through its file API (a sandbox need not have either program), so this applies the same
rules: every ``.gitignore`` in the tree, each for the directory it sits in, the last
matching pattern deciding, ``!`` re-including, a trailing ``/`` matching directories
only, and nothing re-included below an excluded directory (as git).

``.git/`` is always skipped, and so is githi's own ``.githi/`` (kept command output:
pi keeps that in a temp directory, outside what it searches).
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Final

__all__ = ["ALWAYS_SKIPPED", "Ignore", "glob_regex"]

ALWAYS_SKIPPED: Final = frozenset({".git", ".githi"})
"""Directory names never searched, at any depth."""


@lru_cache(maxsize=512)
def glob_regex(pattern: str) -> re.Pattern[str]:
    """A git-style glob as a regex over a ``/``-separated path: ``*`` and ``?`` stay
    within one name, ``**`` crosses directories, ``[...]`` is a class."""
    out: list[str] = []
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if c == "*":
            if pattern.startswith("**/", i):
                out.append("(?:.*/)?")
                i += 3
                continue
            if pattern.startswith("**", i):
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == "[" and (end := pattern.find("]", i + 2)) != -1:
            body = pattern[i + 1 : end]
            if body[0] in "!^":
                body = "^" + body[1:]
            out.append("[" + body.replace("\\", "\\\\") + "]")
            i = end + 1
            continue
        elif c == "\\" and i + 1 < len(pattern):
            out.append(re.escape(pattern[i + 1]))
            i += 2
            continue
        else:
            out.append(re.escape(c))
        i += 1
    return re.compile("".join(out) + r"\Z", re.DOTALL)


@dataclass(frozen=True, slots=True)
class _Rule:
    base: str  # the directory of the .gitignore, workspace-relative ("" for the root)
    regex: re.Pattern[str]
    anchored: bool  # matched against the path below ``base``, else against the name
    negate: bool
    dir_only: bool

    def decide(self, path: str, is_dir: bool) -> bool | None:
        """True (ignored) / False (re-included) when this rule matches ``path``, else None."""
        if self.dir_only and not is_dir:
            return None
        if self.base:
            if not path.startswith(self.base + "/"):
                return None
            path = path[len(self.base) + 1 :]
        subject = path if self.anchored else path.rsplit("/", 1)[-1]
        return None if self.regex.match(subject) is None else not self.negate


def _rules(base: str, text: str) -> list[_Rule]:
    rules: list[_Rule] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.endswith("\\") and raw.endswith(" "):
            line += " "  # an escaped trailing space is part of the pattern
        if not line or line.startswith("#"):
            continue
        negate = line.startswith("!")
        if negate or line.startswith(("\\!", "\\#")):
            line = line[1:]
        dir_only = line.endswith("/")
        line = line.rstrip("/")
        if not line:
            continue
        anchored = "/" in line
        rules.append(_Rule(base, glob_regex(line.lstrip("/")), anchored, negate, dir_only))
    return rules


class Ignore:
    """Which workspace paths a search below ``root`` skips.

    ``root`` is the directory searched, named by the model: it is searched even when a
    rule excludes it (as ``rg pattern node_modules`` searches ``node_modules``); the
    rules apply to what is below it.
    """

    def __init__(self, gitignores: Iterable[tuple[str, str]], *, root: str = "") -> None:
        """``gitignores``: (workspace-relative path of a ``.gitignore``, its text)."""
        ordered = sorted(gitignores, key=lambda item: item[0].count("/"))
        self._rules = [rule for path, text in ordered for rule in _rules(_parent(path), text)]
        self._root = root
        self._dirs: dict[str, bool] = {}

    @classmethod
    async def load(
        cls, files: Sequence[str], read: Callable[[str], Awaitable[bytes]], *, root: str = ""
    ) -> Ignore:
        """The rules from the ``.gitignore`` files among ``files`` that bear on ``root``
        (in it, below it, or above it); one that cannot be read is skipped."""
        found: list[tuple[str, str]] = []
        for path in files:
            if path.rsplit("/", 1)[-1] != ".gitignore" or _skipped_dir(path):
                continue
            base = _parent(path)
            if not (_within(root, base) or _within(base, root)):
                continue
            try:
                data = await read(path)
            except Exception:  # noqa: BLE001, S112 — an unreadable .gitignore excludes nothing
                continue
            found.append((path, data.decode("utf-8", errors="replace")))
        return cls(found, root=root)

    def _decide(self, path: str, is_dir: bool) -> bool:
        ignored = False
        for rule in self._rules:
            verdict = rule.decide(path, is_dir)
            if verdict is not None:
                ignored = verdict
        return ignored

    def dir_ignored(self, path: str) -> bool:
        """Whether directory ``path`` (or one above it) is excluded."""
        if not path or _within(self._root, path):
            return False  # the searched directory and those above it
        if path in self._dirs:
            return self._dirs[path]
        parent = _parent(path)
        ignored = (
            path.rsplit("/", 1)[-1] in ALWAYS_SKIPPED
            or (bool(parent) and self.dir_ignored(parent))
            or self._decide(path, True)
        )
        self._dirs[path] = ignored
        return ignored

    def ignored(self, path: str) -> bool:
        """Whether file ``path`` is excluded, by its own rules or a directory's above it."""
        parent = _parent(path)
        if parent and self.dir_ignored(parent):
            return True
        return self._decide(path, False)


def _parent(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else ""


def _within(path: str, base: str) -> bool:
    """Whether ``path`` is ``base`` or below it (``""`` is the workspace root)."""
    return not base or path == base or path.startswith(base + "/")


def _skipped_dir(path: str) -> bool:
    return any(part in ALWAYS_SKIPPED for part in path.split("/")[:-1])
