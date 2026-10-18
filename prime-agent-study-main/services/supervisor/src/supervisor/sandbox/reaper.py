"""Orphan sandbox reaping — cleanup for sandboxes that outlive their sessions.

When the supervisor process restarts, Docker containers and other long-lived
sandboxes may still be running. This module provides reaping functionality
to clean up orphaned sandboxes on startup.

Reaping Strategy:
    1. Query provider for all managed sandboxes
    2. Look up session status for each sandbox
    3. Reattach active sessions, destroy terminated ones

Thread Safety:
    reap_orphans is async and should be called during supervisor startup.
    It's not safe to call concurrently with session creation.

Reference: Container lifecycle management, crash recovery
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum, auto
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from supervisor.sandbox.base import Sandbox, SandboxProvider

if TYPE_CHECKING:
    from supervisor.sandbox.providers.local_docker import LocalDockerProvider

__all__ = [
    "reap_orphans",
    "ReapStats",
    "SessionStatus",
    "SessionLookup",
    "SandboxReattach",
]

_log = logging.getLogger(__name__)


# =============================================================================
# Types
# =============================================================================

class SessionStatus(StrEnum):
    """Known session states for reaping decisions."""

    ACTIVE = auto()
    TERMINATED = auto()
    FAILED = auto()
    BUDGET_EXCEEDED = auto()
    UNKNOWN = auto()


# Terminal states that trigger sandbox destruction
_TERMINAL_STATES: frozenset[str] = frozenset({
    SessionStatus.TERMINATED,
    SessionStatus.FAILED,
    SessionStatus.BUDGET_EXCEEDED,
    "terminated",
    "failed",
    "budget_exceeded",
})


@runtime_checkable
class SessionLookup(Protocol):
    """Protocol for looking up session status."""

    async def __call__(self, session_id: str) -> str | None:
        """Look up session status by ID.

        Returns:
            Session status string, or None if session unknown.
        """
        ...


@runtime_checkable
class SandboxReattach(Protocol):
    """Protocol for reattaching a sandbox to its session."""

    async def __call__(self, session_id: str, sandbox: Sandbox) -> None:
        """Reattach sandbox to session.

        Args:
            session_id: ID of the session to reattach.
            sandbox: The sandbox to reattach.
        """
        ...


@dataclass(slots=True)
class ReapStats:
    """Statistics from a reaping operation."""

    reattached: int = 0
    destroyed: int = 0
    skipped: int = 0
    errors: int = 0
    _details: list[str] = field(default_factory=list)

    def record_reattach(self, sandbox_id: str, session_id: str) -> None:
        """Record a successful reattachment."""
        self.reattached += 1
        self._details.append(f"reattached {sandbox_id[:12]} -> {session_id}")

    def record_destroy(self, sandbox_id: str, reason: str) -> None:
        """Record a sandbox destruction."""
        self.destroyed += 1
        self._details.append(f"destroyed {sandbox_id[:12]}: {reason}")

    def record_skip(self, sandbox_id: str, reason: str) -> None:
        """Record a skipped sandbox."""
        self.skipped += 1
        self._details.append(f"skipped {sandbox_id[:12]}: {reason}")

    def record_error(self, sandbox_id: str, error: str) -> None:
        """Record an error during processing."""
        self.errors += 1
        self._details.append(f"error {sandbox_id[:12]}: {error}")

    def to_dict(self) -> dict[str, int]:
        """Convert to dict for backward compatibility."""
        return {
            "reattached": self.reattached,
            "destroyed": self.destroyed,
            "skipped": self.skipped,
            "errors": self.errors,
        }

    @property
    def total_processed(self) -> int:
        """Total sandboxes processed."""
        return self.reattached + self.destroyed + self.skipped + self.errors


# =============================================================================
# Reaping Implementation
# =============================================================================

async def _process_sandbox(
    sandbox: Sandbox,
    *,
    lookup_session: SessionLookup,
    reattach: SandboxReattach | None,
    stats: ReapStats,
    session_label: str,
) -> None:
    """Process a single sandbox for reaping.

    Encapsulates the decision logic for what to do with each sandbox.
    """
    sandbox_id = sandbox.info.id
    session_id = sandbox.info.labels.get(session_label)

    # No session label = orphan from before labeling was added
    if not session_id:
        _log.info("destroying unlabeled sandbox=%s", sandbox_id[:12])
        await sandbox.destroy()
        stats.record_destroy(sandbox_id, "no session label")
        return

    # Look up session status
    try:
        status = await lookup_session(session_id)
    except Exception as e:
        _log.warning(
            "error looking up session=%s for sandbox=%s: %s",
            session_id,
            sandbox_id[:12],
            e,
        )
        stats.record_error(sandbox_id, f"lookup failed: {e}")
        return

    # Decide based on status
    if status is None or status in _TERMINAL_STATES:
        _log.info(
            "reaping orphan sandbox=%s session=%s status=%s",
            sandbox_id[:12],
            session_id,
            status or "unknown",
        )
        await sandbox.destroy()
        stats.record_destroy(sandbox_id, f"session {status or 'unknown'}")

    elif status == "active" or status == SessionStatus.ACTIVE:
        if reattach is not None:
            try:
                await reattach(session_id, sandbox)
                stats.record_reattach(sandbox_id, session_id)
                _log.info(
                    "reattached sandbox=%s to session=%s",
                    sandbox_id[:12],
                    session_id,
                )
            except Exception as e:
                _log.warning(
                    "failed to reattach sandbox=%s: %s",
                    sandbox_id[:12],
                    e,
                )
                stats.record_error(sandbox_id, f"reattach failed: {e}")
        else:
            stats.record_reattach(sandbox_id, session_id)
            _log.info(
                "sandbox=%s is active (no reattach callback)",
                sandbox_id[:12],
            )
    else:
        # Unknown/unexpected status - skip but log
        _log.info(
            "skipping sandbox=%s with unknown status=%s",
            sandbox_id[:12],
            status,
        )
        stats.record_skip(sandbox_id, f"unknown status: {status}")


async def reap_orphans(
    provider: SandboxProvider,
    *,
    lookup_session: SessionLookup,
    reattach: SandboxReattach | None = None,
    concurrency: int = 10,
) -> ReapStats:
    """Reap orphaned sandboxes on supervisor startup.

    Queries the provider for managed sandboxes, looks up their session
    status, and either reattaches active ones or destroys terminated ones.

    Args:
        provider: Sandbox provider to query.
        lookup_session: Callback to look up session status.
        reattach: Optional callback to reattach active sandboxes.
        concurrency: Max concurrent sandbox processing (default 10).

    Returns:
        Statistics about the reaping operation.

    Note:
        Only LocalDockerProvider supports listing managed sandboxes.
        Other providers return empty stats immediately.

    Example:
        >>> async def lookup(sid: str) -> str | None:
        ...     return await db.get_session_status(sid)
        ...
        >>> stats = await reap_orphans(provider, lookup_session=lookup)
        >>> print(f"Destroyed {stats.destroyed} orphaned sandboxes")
    """
    stats = ReapStats()

    # Import here to avoid circular dependency
    from supervisor.sandbox.providers.local_docker import (
        LABEL_SESSION,
        LocalDockerProvider,
    )

    # Only LocalDockerProvider supports listing
    if not isinstance(provider, LocalDockerProvider):
        _log.debug("provider %s does not support orphan listing", provider.name)
        return stats

    # Get all managed sandboxes
    try:
        managed = await provider.list_managed()
    except Exception as e:
        _log.error("failed to list managed sandboxes: %s", e)
        stats.record_error("provider", f"list_managed failed: {e}")
        return stats

    if not managed:
        _log.debug("no managed sandboxes found")
        return stats

    _log.info("found %d managed sandboxes to process", len(managed))

    # Process with bounded concurrency using semaphore
    semaphore = asyncio.Semaphore(concurrency)

    async def process_with_semaphore(sandbox: Sandbox) -> None:
        async with semaphore:
            try:
                await _process_sandbox(
                    sandbox,
                    lookup_session=lookup_session,
                    reattach=reattach,
                    stats=stats,
                    session_label=LABEL_SESSION,
                )
            except Exception as e:
                _log.error(
                    "unexpected error processing sandbox=%s: %s",
                    sandbox.info.id[:12],
                    e,
                )
                stats.record_error(sandbox.info.id, str(e))

    # Process all sandboxes concurrently
    await asyncio.gather(
        *(process_with_semaphore(sbx) for sbx in managed),
        return_exceptions=True,
    )

    _log.info(
        "reaping complete: reattached=%d destroyed=%d skipped=%d errors=%d",
        stats.reattached,
        stats.destroyed,
        stats.skipped,
        stats.errors,
    )

    return stats
