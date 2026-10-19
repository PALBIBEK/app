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
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from supervisor.sandbox.base import Sandbox, SandboxProvider

__all__ = [
    "PROVIDER_CAPABILITIES",
    "SandboxCapability",
    "capabilities_lost_moving_to",
    "detect_capabilities",
    "detect_provider_capabilities",
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


def detect_capabilities(sandbox: Sandbox) -> frozenset[SandboxCapability]:
    """Detect capabilities of a sandbox instance at runtime.

    Combines static provider registry with runtime introspection.
    This catches cases where providers gain/lose features dynamically.

    Args:
        sandbox: Sandbox instance to inspect.

    Returns:
        Set of capabilities the sandbox supports.

    Example:
        >>> caps = detect_capabilities(sandbox)
        >>> if SandboxCapability.SNAPSHOT in caps:
        ...     snapshot_id = await sandbox.pause()
    """
    caps: set[SandboxCapability] = set()
    info = sandbox.info

    if info.supports_snapshot:
        caps.add(SandboxCapability.SNAPSHOT)

    if info.supports_workspace_sync:
        caps.add(SandboxCapability.WORKSPACE_SYNC)

    if hasattr(sandbox, "spawn") and callable(getattr(sandbox, "spawn", None)):
        caps.add(SandboxCapability.PROCESS_SESSIONS)

    if hasattr(sandbox, "health_check") and callable(getattr(sandbox, "health_check", None)):
        caps.add(SandboxCapability.HEALTH_CHECK)

    provider_caps = PROVIDER_CAPABILITIES.get(info.provider, frozenset())
    caps.update(provider_caps)

    return frozenset(caps)


def detect_provider_capabilities(provider: SandboxProvider) -> frozenset[SandboxCapability]:
    """Get static capabilities for a provider.

    Uses the static registry. For runtime detection, use detect_capabilities()
    on a sandbox instance.

    Args:
        provider: Provider to look up.

    Returns:
        Set of capabilities the provider supports.
    """
    return PROVIDER_CAPABILITIES.get(provider.name, frozenset())


def capabilities_lost_moving_to(
    from_caps: frozenset[SandboxCapability],
    to_caps: frozenset[SandboxCapability],
) -> tuple[str, ...]:
    """Calculate capabilities lost when migrating.

    Args:
        from_caps: Capabilities of source backend.
        to_caps: Capabilities of destination backend.

    Returns:
        Tuple of capability names that would be lost.

    Example:
        >>> from_caps = {SandboxCapability.SNAPSHOT, SandboxCapability.PROCESS_SESSIONS}
        >>> to_caps = {SandboxCapability.PROCESS_SESSIONS}
        >>> lost = capabilities_lost_moving_to(from_caps, to_caps)
        >>> lost
        ('snapshot',)
    """
    lost = from_caps - to_caps
    return tuple(sorted(cap.value for cap in lost))


def capabilities_lost_for_backends(
    from_backend: str,
    to_backend: str,
) -> tuple[str, ...]:
    """Calculate capabilities lost when migrating between backends.

    Convenience wrapper using static provider registry.

    Args:
        from_backend: Source backend name.
        to_backend: Destination backend name.

    Returns:
        Tuple of capability names that would be lost.

    Example:
        >>> lost = capabilities_lost_for_backends("e2b", "local_docker")
        >>> "snapshot" in lost
        True
    """
    from_caps = PROVIDER_CAPABILITIES.get(from_backend, frozenset())
    to_caps = PROVIDER_CAPABILITIES.get(to_backend, frozenset())
    return capabilities_lost_moving_to(from_caps, to_caps)


def aggregate_capabilities(
    backends: dict[str, SandboxProvider],
) -> frozenset[SandboxCapability]:
    """Aggregate capabilities from all backends.

    Used by router to expose optional methods only if ANY backend supports them.

    Args:
        backends: Dict of backend name to provider.

    Returns:
        Union of all backend capabilities.

    Example:
        >>> caps = aggregate_capabilities({"e2b": e2b_provider, "docker": docker_provider})
        >>> SandboxCapability.SNAPSHOT in caps  # True if any backend has it
        True
    """
    all_caps: set[SandboxCapability] = set()
    for provider in backends.values():
        all_caps.update(PROVIDER_CAPABILITIES.get(provider.name, frozenset()))
    return frozenset(all_caps)


def format_capability_loss(lost: tuple[str, ...]) -> str:
    """Format capability loss for human-readable display.

    Args:
        lost: Tuple of lost capability names.

    Returns:
        Formatted string describing the loss.

    Example:
        >>> format_capability_loss(("snapshot", "blob_staging"))
        'Will lose: snapshot, blob_staging'
    """
    if not lost:
        return "No capabilities will be lost"
    return f"Will lose: {', '.join(lost)}"


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


def describe_capability(cap: SandboxCapability) -> str:
    """Get human-readable description of a capability.

    Args:
        cap: Capability to describe.

    Returns:
        Description string.
    """
    return CAPABILITY_DESCRIPTIONS.get(cap, cap.value)
