"""Business logic layer for session operations.

This module provides the core services for session management:
- SessionManager: Main orchestrator for lifecycle operations
- Recovery: Decision engine for session resume
- LiveHub: SSE event fan-out to subscribers
- Lifecycle operations: Idle reaping, drain
"""

from __future__ import annotations

from supervisor.session.services.recovery import (
    Recovery,
    decide_unpaired,
    pinned_manifest,
)
from supervisor.session.services.fanout import (
    LiveHub,
    LiveSubscription,
    is_dropped_sentinel,
)
from supervisor.session.services.lifecycle import (
    reap_idle,
    drain_for_deploy,
)
from supervisor.session.services.workspace import (
    workspaces_root,
    session_workspace,
)

__all__ = [
    # Recovery
    "Recovery",
    "decide_unpaired",
    "pinned_manifest",
    # Fanout
    "LiveHub",
    "LiveSubscription",
    "is_dropped_sentinel",
    # Lifecycle
    "reap_idle",
    "drain_for_deploy",
    # Workspace
    "workspaces_root",
    "session_workspace",
]
