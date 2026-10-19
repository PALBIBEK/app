"""Session configuration loaded from environment variables.

All settings have sensible defaults and can be overridden via
environment variables for deployment flexibility.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

__all__ = [
    "SessionSettings",
    "get_settings",
]


@dataclass(frozen=True, slots=True)
class SessionSettings:
    """Immutable session configuration.

    Attributes:
        workspaces_root: Base directory for per-session workspaces
        live_queue_max: Maximum events in SSE subscriber queue
        idle_timeout_seconds: Time before idle session is reaped
        default_approval_timeout: Timeout for human approval requests
        mutating_tools: Tool names that modify workspace state
    """

    workspaces_root: Path
    live_queue_max: int
    idle_timeout_seconds: int
    default_approval_timeout: float
    mutating_tools: frozenset[str]

    @classmethod
    def from_env(cls) -> SessionSettings:
        """Load settings from environment with defaults.

        Environment Variables:
            YAL_SESSION_WORKSPACES: Base path for session workspaces
            SESSION_LIVE_QUEUE_MAX: Max events per SSE queue (default: 256)
            SESSION_IDLE_TIMEOUT: Idle reap timeout in seconds (default: 3600)
            APPROVAL_TIMEOUT: Human approval timeout in seconds (default: 300)

        Returns:
            SessionSettings instance with loaded values
        """
        # Workspace root
        workspaces_env = os.environ.get("YAL_SESSION_WORKSPACES")
        if workspaces_env:
            workspaces_root = Path(workspaces_env)
        else:
            workspaces_root = Path(tempfile.gettempdir()) / "yal-sessions"

        return cls(
            workspaces_root=workspaces_root,
            live_queue_max=int(os.environ.get("SESSION_LIVE_QUEUE_MAX", "256")),
            idle_timeout_seconds=int(os.environ.get("SESSION_IDLE_TIMEOUT", "3600")),
            default_approval_timeout=float(os.environ.get("APPROVAL_TIMEOUT", "300")),
            mutating_tools=frozenset({"write", "edit", "edit_diff"}),
        )

    def ensure_workspaces_root(self) -> Path:
        """Ensure workspaces root directory exists.

        Returns:
            The workspaces root path after creation
        """
        self.workspaces_root.mkdir(parents=True, exist_ok=True)
        return self.workspaces_root

    def session_workspace(self, session_id: str) -> Path:
        """Get workspace path for a specific session.

        Args:
            session_id: The session identifier

        Returns:
            Path to session's workspace directory (created if needed)
        """
        path = self.workspaces_root / session_id
        path.mkdir(parents=True, exist_ok=True)
        return path


@lru_cache(maxsize=1)
def get_settings() -> SessionSettings:
    """Get cached singleton settings instance.

    Settings are loaded once and cached for the process lifetime.
    Use this function to access settings throughout the application.

    Returns:
        Cached SessionSettings instance

    Example:
        settings = get_settings()
        workspace = settings.session_workspace("sess-123")
    """
    return SessionSettings.from_env()
