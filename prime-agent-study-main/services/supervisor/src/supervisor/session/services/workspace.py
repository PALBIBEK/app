"""Host-side session workspace path management.

Provides workspace directory creation and management for
harness bind files and session-specific storage.
"""

from __future__ import annotations

import logging
from pathlib import Path

from supervisor.session.config.settings import get_settings

__all__ = [
    "workspaces_root",
    "session_workspace",
]

logger = logging.getLogger(__name__)


def workspaces_root() -> Path:
    """Get the root directory for per-session workspaces.

    Override with YAL_SESSION_WORKSPACES environment variable.
    Creates the directory if it doesn't exist.

    Returns:
        Path to the workspaces root directory
    """
    settings = get_settings()
    return settings.ensure_workspaces_root()


def session_workspace(session_id: str) -> Path:
    """Get the workspace directory for a specific session.

    Creates the directory if it doesn't exist.

    Args:
        session_id: The session identifier

    Returns:
        Path to the session's workspace directory
    """
    settings = get_settings()
    return settings.session_workspace(session_id)
