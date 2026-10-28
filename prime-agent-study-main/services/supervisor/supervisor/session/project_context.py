"""The project's own instructions for the agent: pi's ``AGENTS.md`` / ``CLAUDE.md``.

pi reads the first of ``AGENTS.override.md``, ``AGENTS.md`` … ``CLAUDE.MD`` it finds in
the working directory (and its parents) into the system prompt, so a repository can
tell any agent how to build, test and behave in it. githi's working directory is the
workspace root: the file is read there, from the live sandbox when there is one, else
from what the session's files were saved to (its checkpoints, then its workspace
scope), so a first run reads it without building a sandbox.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from supervisor.hands.checkpoint import checkpoint_scope
from supervisor.tools.errors import FileMissing
from supervisor.tools.workspace import SandboxWorkspace

if TYPE_CHECKING:
    from supervisor.session.manager_base import SessionManagerBase

__all__ = ["CONTEXT_FILES", "MAX_CONTEXT_BYTES", "ProjectFile", "read_project_context"]

logger = logging.getLogger(__name__)

CONTEXT_FILES: Final = ("AGENTS.override.md", "AGENTS.md", "AGENTS.MD", "CLAUDE.md", "CLAUDE.MD")
"""pi's project instruction files, in the order it looks for them; the first found is used."""

MAX_CONTEXT_BYTES: Final = 64 * 1024
"""A project file is instructions, not data: past this it is cut, and says so."""


@dataclass(frozen=True, slots=True)
class ProjectFile:
    path: str
    text: str

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()[:32]


def _text(name: str, data: bytes) -> ProjectFile:
    cut = len(data) > MAX_CONTEXT_BYTES
    text = data[:MAX_CONTEXT_BYTES].decode("utf-8", errors="replace").lstrip("﻿")
    if cut:
        text += f"\n\n[{name} is longer than {MAX_CONTEXT_BYTES // 1024} KB; the rest was left out]"
    return ProjectFile(name, text)


async def read_project_context(manager: SessionManagerBase, session_id: str) -> ProjectFile | None:
    """The session's project instructions, if its workspace has any; never raises."""
    try:
        if (sandbox := manager._hands.sandbox(session_id)) is not None:
            workspace = SandboxWorkspace(sandbox)
            for name in CONTEXT_FILES:
                try:
                    return _text(name, await workspace.read_bytes(name))
                except FileMissing:
                    continue
            return None
        store = manager._workspace_store
        if store is None:
            return None
        state = manager._workspace.get(session_id)
        scopes = [checkpoint_scope(session_id), *([state.scope_id] if state and state.scope_id else [])]
        for scope in scopes:
            for name in CONTEXT_FILES:
                record = await store.stat(scope, name)
                if record is not None and not record.deleted:
                    return _text(name, await store.objects.get(record.object_key))  # type: ignore[attr-defined]
    except Exception:
        logger.warning("project context could not be read session=%s", session_id, exc_info=True)
    return None
