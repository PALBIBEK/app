"""Host-side session workspace paths for harness bind files."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def workspaces_root() -> Path:
    """Root for per-session directories (override with YAL_SESSION_WORKSPACES)."""
    env = os.environ.get("YAL_SESSION_WORKSPACES")
    if env:
        root = Path(env)
    else:
        root = Path(tempfile.gettempdir()) / "yal-sessions"
    root.mkdir(parents=True, exist_ok=True)
    return root


def session_workspace(session_id: str) -> Path:
    path = workspaces_root() / session_id
    path.mkdir(parents=True, exist_ok=True)
    return path
