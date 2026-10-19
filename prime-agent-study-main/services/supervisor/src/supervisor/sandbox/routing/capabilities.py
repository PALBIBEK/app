"""Sandbox capability detection and tracking.

This module provides:
- SandboxCapability: Enum of features sandboxes may support
- detect_capabilities(): Runtime capability detection
- capabilities_lost_moving_to(): Migration impact analysis
- PROVIDER_CAPABILITIES: Static registry of known provider capabilities

Capabilities are used to:
1. Expose conditional features (only if ANY backend supports)
2. Block migrations that would lose critical capabilities
3. Track what was lost during migration for audit

Reference: QM's capability detection pattern (supportsProcessSessions, etc.)
"""

from __future__ import annotations

from enum import StrEnum, auto
from typing import Final

__all__ = [
    "PROVIDER_CAPABILITIES",
    "SandboxCapability",
]


class SandboxCapability(StrEnum):
    """Capabilities that sandboxes may support.

    These are optional features that vary by backend:
    - Some require VM isolation (e.g., snapshots)
    - Some require specific infrastructure (e.g., egress control)
    - Some are development-only (e.g., local process sessions)
    """

    # Core capabilities
    PROCESS_SESSIONS = auto()
    WORKSPACE_SYNC = auto()
    SNAPSHOT = auto()
    BLOB_STAGING = auto()
    HEALTH_CHECK = auto()

    # Egress control
    EGRESS_DOMAIN = auto()
    EGRESS_IP_PORT = auto()

    # Computer management (QM features)
    COMPUTER_BACKUP = auto()
    COMPUTER_STATUS = auto()
    COMPUTER_RESTART = auto()

    # Layer management
    RO_LAYERS = auto()

    # Sandbox types
    SCRATCH_SANDBOXES = auto()

    # Lifecycle management
    DEEP_IDLE_REAP = auto()


PROVIDER_CAPABILITIES: Final[dict[str, frozenset[SandboxCapability]]] = {
    "e2b": frozenset({
        # Core
        SandboxCapability.PROCESS_SESSIONS,
        SandboxCapability.SNAPSHOT,
        SandboxCapability.BLOB_STAGING,
        SandboxCapability.HEALTH_CHECK,
        # Computer management
        SandboxCapability.COMPUTER_BACKUP,
        SandboxCapability.COMPUTER_STATUS,
        SandboxCapability.COMPUTER_RESTART,
        # Layers
        SandboxCapability.RO_LAYERS,
        # Sandbox types
        SandboxCapability.SCRATCH_SANDBOXES,
    }),
    "local_docker": frozenset({
        # Core
        SandboxCapability.PROCESS_SESSIONS,
        SandboxCapability.WORKSPACE_SYNC,
        SandboxCapability.HEALTH_CHECK,
        # Sandbox types
        SandboxCapability.SCRATCH_SANDBOXES,
    }),
    "local_process": frozenset({
        # Core
        SandboxCapability.PROCESS_SESSIONS,
        SandboxCapability.WORKSPACE_SYNC,
        # Sandbox types
        SandboxCapability.SCRATCH_SANDBOXES,
    }),
    "firecracker": frozenset({
        # Core
        SandboxCapability.PROCESS_SESSIONS,
        SandboxCapability.SNAPSHOT,
        SandboxCapability.HEALTH_CHECK,
        # Egress
        SandboxCapability.EGRESS_DOMAIN,
        # Computer management
        SandboxCapability.COMPUTER_STATUS,
        SandboxCapability.COMPUTER_RESTART,
        # Layers
        SandboxCapability.RO_LAYERS,
        # Sandbox types
        SandboxCapability.SCRATCH_SANDBOXES,
        # Lifecycle
        SandboxCapability.DEEP_IDLE_REAP,
    }),
    "none": frozenset(),
}


CAPABILITY_DESCRIPTIONS: Final[dict[SandboxCapability, str]] = {
    # Core
    SandboxCapability.PROCESS_SESSIONS: "Long-running background processes",
    SandboxCapability.WORKSPACE_SYNC: "Durable workspace (hydrate/sync)",
    SandboxCapability.SNAPSHOT: "Pause/resume via snapshot",
    SandboxCapability.BLOB_STAGING: "Efficient file transfer",
    SandboxCapability.HEALTH_CHECK: "Provider health monitoring",
    # Egress
    SandboxCapability.EGRESS_DOMAIN: "Domain-level egress control",
    SandboxCapability.EGRESS_IP_PORT: "IP:port-level egress control",
    # Computer management
    SandboxCapability.COMPUTER_BACKUP: "Computer backup (workspace/home)",
    SandboxCapability.COMPUTER_STATUS: "Computer status monitoring",
    SandboxCapability.COMPUTER_RESTART: "Computer restart capability",
    # Layers
    SandboxCapability.RO_LAYERS: "Read-only layer materialization",
    # Sandbox types
    SandboxCapability.SCRATCH_SANDBOXES: "Ephemeral scratch sandboxes",
    # Lifecycle
    SandboxCapability.DEEP_IDLE_REAP: "Deep idle reaping with snapshots",
}


