"""Deep idle reaping with configurable thresholds.

This module provides sophisticated idle sandbox reaping that tracks scope
activity and supports optional snapshot creation before reaping.

Design (matches QM's idle reaping):
    1. Configurable soft/hard idle thresholds
    2. Production vs development mode presets
    3. Snapshot before reap for forensics
    4. Activity tracking via pluggable store

Security:
    - Snapshots preserve state for incident response
    - Activity tracking enables audit trails
    - Configurable thresholds prevent resource exhaustion

Reference:
    - QM: reaper.ts, sandbox.ts idle handling
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import StrEnum, auto
from typing import TYPE_CHECKING, Final, Protocol, runtime_checkable

if TYPE_CHECKING:
    from supervisor.sandbox.backup import BackupResult

_log = logging.getLogger(__name__)

__all__ = [
    "IdleReapMode",
    "IdleThresholds",
    "ScopeActivity",
    "ReapDecision",
    "ReapStats",
    "IdleReapError",
    "ActivityStore",
    "IdleReaper",
    "create_idle_reaper",
    "SOFT_IDLE_PRODUCTION_MS",
    "HARD_IDLE_PRODUCTION_MS",
    "SOFT_IDLE_DEVELOPMENT_MS",
    "HARD_IDLE_DEVELOPMENT_MS",
]


# =============================================================================
# Constants
# =============================================================================

# Production thresholds: aggressive to minimize resource usage
SOFT_IDLE_PRODUCTION_MS: Final[int] = 5 * 60 * 1000  # 5 minutes
HARD_IDLE_PRODUCTION_MS: Final[int] = 15 * 60 * 1000  # 15 minutes

# Development thresholds: relaxed for interactive work
SOFT_IDLE_DEVELOPMENT_MS: Final[int] = 30 * 60 * 1000  # 30 minutes
HARD_IDLE_DEVELOPMENT_MS: Final[int] = 60 * 60 * 1000  # 1 hour

# Reaping interval
REAP_INTERVAL_MS: Final[int] = 60 * 1000  # 1 minute


# =============================================================================
# Enums
# =============================================================================


class IdleReapMode(StrEnum):
    """Idle reaping operational modes."""

    PRODUCTION = auto()
    DEVELOPMENT = auto()


class ReapAction(StrEnum):
    """Actions taken during reaping."""

    NONE = auto()
    WARN = auto()
    SNAPSHOT = auto()
    REAP = auto()


# =============================================================================
# Errors
# =============================================================================


class IdleReapError(Exception):
    """Idle reaping operation failed.

    Attributes:
        message: Error description.
        scope_id: Affected scope, if known.
        action: Action that failed.
        retryable: Whether the operation can be retried.
    """

    def __init__(
        self,
        message: str,
        *,
        scope_id: str | None = None,
        action: str = "unknown",
        retryable: bool = True,
    ) -> None:
        super().__init__(message)
        self.scope_id = scope_id
        self.action = action
        self.retryable = retryable


# =============================================================================
# Data Classes
# =============================================================================


@dataclass(frozen=True, slots=True)
class IdleThresholds:
    """Configurable idle thresholds for reaping.

    Attributes:
        soft_idle_ms: Time after which to warn/prepare for reaping.
        hard_idle_ms: Time after which to actually reap.
        snapshot_before_reap: Whether to create snapshot before reaping.
        warn_callback: Whether to invoke warning callback at soft threshold.
    """

    soft_idle_ms: int
    hard_idle_ms: int
    snapshot_before_reap: bool = True
    warn_callback: bool = True

    def __post_init__(self) -> None:
        """Validate thresholds."""
        if self.soft_idle_ms >= self.hard_idle_ms:
            raise ValueError("soft_idle_ms must be less than hard_idle_ms")
        if self.soft_idle_ms <= 0:
            raise ValueError("soft_idle_ms must be positive")

    @classmethod
    def production(cls) -> IdleThresholds:
        """Create production thresholds (aggressive).

        Returns:
            IdleThresholds with 5m soft, 15m hard, snapshot enabled.
        """
        return cls(
            soft_idle_ms=SOFT_IDLE_PRODUCTION_MS,
            hard_idle_ms=HARD_IDLE_PRODUCTION_MS,
            snapshot_before_reap=True,
            warn_callback=True,
        )

    @classmethod
    def development(cls) -> IdleThresholds:
        """Create development thresholds (relaxed).

        Returns:
            IdleThresholds with 30m soft, 1h hard, snapshot enabled.
        """
        return cls(
            soft_idle_ms=SOFT_IDLE_DEVELOPMENT_MS,
            hard_idle_ms=HARD_IDLE_DEVELOPMENT_MS,
            snapshot_before_reap=True,
            warn_callback=True,
        )

    @classmethod
    def from_mode(cls, mode: IdleReapMode) -> IdleThresholds:
        """Create thresholds from mode.

        Args:
            mode: Operational mode.

        Returns:
            Appropriate IdleThresholds for the mode.
        """
        if mode == IdleReapMode.PRODUCTION:
            return cls.production()
        return cls.development()


@dataclass(slots=True)
class ScopeActivity:
    """Activity tracking for a scope.

    Attributes:
        scope_id: Scope identifier.
        last_activity_ms: Timestamp of last activity in milliseconds.
        sandbox_id: Associated sandbox ID, if any.
        warned_at_ms: When soft idle warning was issued, if any.
    """

    scope_id: str
    last_activity_ms: int
    sandbox_id: str | None = None
    warned_at_ms: int | None = None

    def idle_duration_ms(self, now_ms: int | None = None) -> int:
        """Calculate current idle duration.

        Args:
            now_ms: Current time in milliseconds. Defaults to now.

        Returns:
            Idle duration in milliseconds.
        """
        if now_ms is None:
            now_ms = int(time.time() * 1000)
        return now_ms - self.last_activity_ms

    def touch(self, sandbox_id: str | None = None) -> None:
        """Update last activity time.

        Args:
            sandbox_id: Optional sandbox ID to associate.
        """
        self.last_activity_ms = int(time.time() * 1000)
        self.warned_at_ms = None  # Reset warning on activity
        if sandbox_id is not None:
            self.sandbox_id = sandbox_id


@dataclass(frozen=True, slots=True)
class ReapDecision:
    """Decision for a single scope during reaping.

    Attributes:
        scope_id: Scope identifier.
        action: Action to take.
        idle_duration_ms: How long the scope has been idle.
        sandbox_id: Associated sandbox, if any.
    """

    scope_id: str
    action: ReapAction
    idle_duration_ms: int
    sandbox_id: str | None = None


@dataclass(frozen=True, slots=True)
class ReapStats:
    """Statistics from a reaping cycle.

    Attributes:
        scopes_checked: Number of scopes evaluated.
        sandboxes_reaped: Number of sandboxes terminated.
        snapshots_taken: Number of snapshots created.
        warnings_issued: Number of soft idle warnings issued.
        errors: Number of errors encountered.
        duration_ms: Time taken for reaping cycle.
    """

    scopes_checked: int
    sandboxes_reaped: int
    snapshots_taken: int
    warnings_issued: int
    errors: int
    duration_ms: int


@dataclass(frozen=True, slots=True)
class ReapConfig:
    """Configuration for idle reaper.

    Attributes:
        thresholds: Idle thresholds to use.
        dry_run: If True, log but don't actually reap.
        max_concurrent_reaps: Maximum concurrent reap operations.
    """

    thresholds: IdleThresholds
    dry_run: bool = False
    max_concurrent_reaps: int = 5


# =============================================================================
# Protocols
# =============================================================================


@runtime_checkable
class ActivityStore(Protocol):
    """Protocol for activity storage.

    The control plane implements this to track scope activity
    for idle reaping decisions.
    """

    async def record_activity(
        self,
        scope_id: str,
        sandbox_id: str | None = None,
    ) -> None:
        """Record activity for a scope.

        Args:
            scope_id: Scope that had activity.
            sandbox_id: Associated sandbox, if any.
        """
        ...

    async def get_activity(self, scope_id: str) -> ScopeActivity | None:
        """Get activity record for a scope.

        Args:
            scope_id: Scope to look up.

        Returns:
            ScopeActivity or None if not tracked.
        """
        ...

    async def iter_all_scopes(self) -> AsyncIterator[ScopeActivity]:
        """Iterate over all tracked scopes.

        Yields:
            ScopeActivity for each tracked scope.
        """
        ...

    async def remove_scope(self, scope_id: str) -> None:
        """Remove a scope from tracking.

        Args:
            scope_id: Scope to remove.
        """
        ...

    async def mark_warned(self, scope_id: str) -> None:
        """Mark a scope as having received soft idle warning.

        Args:
            scope_id: Scope that was warned.
        """
        ...


@runtime_checkable
class SandboxTerminator(Protocol):
    """Protocol for terminating sandboxes.

    Injected dependency for actually stopping sandboxes.
    """

    async def terminate_sandbox(
        self,
        sandbox_id: str,
        *,
        reason: str,
    ) -> None:
        """Terminate a sandbox.

        Args:
            sandbox_id: Sandbox to terminate.
            reason: Reason for termination.
        """
        ...


@runtime_checkable
class SnapshotCreator(Protocol):
    """Protocol for creating snapshots before reaping.

    Optional dependency for forensic preservation.
    """

    async def create_snapshot(
        self,
        sandbox_id: str,
        *,
        reason: str,
    ) -> str:
        """Create a snapshot of sandbox state.

        Args:
            sandbox_id: Sandbox to snapshot.
            reason: Reason for snapshot.

        Returns:
            Snapshot identifier.
        """
        ...


@runtime_checkable
class IdleWarningCallback(Protocol):
    """Protocol for soft idle warning callbacks.

    Called when a scope crosses the soft idle threshold.
    """

    async def on_idle_warning(
        self,
        scope_id: str,
        idle_duration_ms: int,
        hard_threshold_ms: int,
    ) -> None:
        """Handle soft idle warning.

        Args:
            scope_id: Scope that is idle.
            idle_duration_ms: How long it has been idle.
            hard_threshold_ms: When hard reap will occur.
        """
        ...


# =============================================================================
# Abstract Base
# =============================================================================


class IdleReaper(ABC):
    """Abstract base for idle reaping operations.

    Implementations evaluate scope activity and reap idle sandboxes
    according to configured thresholds.
    """

    __slots__ = ()

    @abstractmethod
    async def evaluate_scope(
        self,
        activity: ScopeActivity,
        *,
        now_ms: int | None = None,
    ) -> ReapDecision:
        """Evaluate a single scope for reaping.

        Args:
            activity: Activity record for the scope.
            now_ms: Current time in milliseconds.

        Returns:
            ReapDecision indicating what action to take.
        """
        ...

    @abstractmethod
    async def reap_idle_sandboxes(self) -> ReapStats:
        """Run a full reaping cycle.

        Iterates all tracked scopes, evaluates each,
        and takes appropriate action.

        Returns:
            ReapStats with cycle results.
        """
        ...

    @abstractmethod
    async def record_activity(
        self,
        scope_id: str,
        sandbox_id: str | None = None,
    ) -> None:
        """Record activity for a scope.

        Should be called on any scope activity to reset idle timer.

        Args:
            scope_id: Scope that had activity.
            sandbox_id: Associated sandbox, if any.
        """
        ...


# =============================================================================
# Implementation
# =============================================================================


class ConfigurableIdleReaper(IdleReaper):
    """Idle reaper with configurable thresholds.

    Matches QM's sophisticated idle reaping:
    - Soft threshold triggers warning
    - Hard threshold triggers reap
    - Optional snapshot before reap
    """

    __slots__ = (
        "_config",
        "_activity_store",
        "_terminator",
        "_snapshot_creator",
        "_warning_callback",
    )

    def __init__(
        self,
        *,
        config: ReapConfig,
        activity_store: ActivityStore,
        terminator: SandboxTerminator,
        snapshot_creator: SnapshotCreator | None = None,
        warning_callback: IdleWarningCallback | None = None,
    ) -> None:
        """Initialize idle reaper.

        Args:
            config: Reaper configuration.
            activity_store: Store for activity tracking.
            terminator: Protocol for terminating sandboxes.
            snapshot_creator: Optional protocol for snapshots.
            warning_callback: Optional callback for soft idle warnings.
        """
        self._config = config
        self._activity_store = activity_store
        self._terminator = terminator
        self._snapshot_creator = snapshot_creator
        self._warning_callback = warning_callback

    async def evaluate_scope(
        self,
        activity: ScopeActivity,
        *,
        now_ms: int | None = None,
    ) -> ReapDecision:
        """Evaluate scope against thresholds."""
        if now_ms is None:
            now_ms = int(time.time() * 1000)

        idle_ms = activity.idle_duration_ms(now_ms)
        thresholds = self._config.thresholds

        # Check hard threshold first
        if idle_ms >= thresholds.hard_idle_ms:
            action = ReapAction.REAP
            if thresholds.snapshot_before_reap and activity.sandbox_id:
                action = ReapAction.SNAPSHOT  # Will reap after snapshot
        # Check soft threshold
        elif idle_ms >= thresholds.soft_idle_ms:
            # Only warn if we haven't already
            if activity.warned_at_ms is None:
                action = ReapAction.WARN
            else:
                action = ReapAction.NONE
        else:
            action = ReapAction.NONE

        return ReapDecision(
            scope_id=activity.scope_id,
            action=action,
            idle_duration_ms=idle_ms,
            sandbox_id=activity.sandbox_id,
        )

    async def reap_idle_sandboxes(self) -> ReapStats:
        """Run full reaping cycle."""
        start_ms = int(time.time() * 1000)
        now_ms = start_ms

        scopes_checked = 0
        sandboxes_reaped = 0
        snapshots_taken = 0
        warnings_issued = 0
        errors = 0

        async for activity in self._activity_store.iter_all_scopes():
            scopes_checked += 1

            try:
                decision = await self.evaluate_scope(activity, now_ms=now_ms)

                if decision.action == ReapAction.NONE:
                    continue

                if decision.action == ReapAction.WARN:
                    await self._handle_warning(activity, decision)
                    warnings_issued += 1

                elif decision.action in (ReapAction.SNAPSHOT, ReapAction.REAP):
                    # Take snapshot if configured and we have a snapshot creator
                    if (
                        decision.action == ReapAction.SNAPSHOT
                        and self._snapshot_creator
                        and decision.sandbox_id
                    ):
                        if not self._config.dry_run:
                            try:
                                await self._snapshot_creator.create_snapshot(
                                    decision.sandbox_id,
                                    reason=f"idle_reap_{decision.idle_duration_ms}ms",
                                )
                                snapshots_taken += 1
                            except Exception as e:
                                _log.warning(
                                    "snapshot failed for %s: %s",
                                    decision.sandbox_id,
                                    e,
                                )
                                errors += 1
                        else:
                            _log.info(
                                "[dry-run] would snapshot %s",
                                decision.sandbox_id,
                            )

                    # Reap the sandbox
                    if decision.sandbox_id:
                        if not self._config.dry_run:
                            try:
                                await self._terminator.terminate_sandbox(
                                    decision.sandbox_id,
                                    reason=f"idle_reap_{decision.idle_duration_ms}ms",
                                )
                                await self._activity_store.remove_scope(
                                    decision.scope_id
                                )
                                sandboxes_reaped += 1
                                _log.info(
                                    "reaped idle sandbox %s (scope=%s, idle=%dms)",
                                    decision.sandbox_id,
                                    decision.scope_id,
                                    decision.idle_duration_ms,
                                )
                            except Exception as e:
                                _log.error(
                                    "failed to reap %s: %s",
                                    decision.sandbox_id,
                                    e,
                                )
                                errors += 1
                        else:
                            _log.info(
                                "[dry-run] would reap %s (idle=%dms)",
                                decision.sandbox_id,
                                decision.idle_duration_ms,
                            )

            except Exception as e:
                _log.error(
                    "error evaluating scope %s: %s",
                    activity.scope_id,
                    e,
                )
                errors += 1

        duration_ms = int(time.time() * 1000) - start_ms

        stats = ReapStats(
            scopes_checked=scopes_checked,
            sandboxes_reaped=sandboxes_reaped,
            snapshots_taken=snapshots_taken,
            warnings_issued=warnings_issued,
            errors=errors,
            duration_ms=duration_ms,
        )

        _log.debug(
            "reap cycle: checked=%d, reaped=%d, snapshots=%d, warns=%d, errors=%d, duration=%dms",
            stats.scopes_checked,
            stats.sandboxes_reaped,
            stats.snapshots_taken,
            stats.warnings_issued,
            stats.errors,
            stats.duration_ms,
        )

        return stats

    async def _handle_warning(
        self,
        activity: ScopeActivity,
        decision: ReapDecision,
    ) -> None:
        """Handle soft idle warning."""
        if self._config.dry_run:
            _log.info(
                "[dry-run] would warn %s (idle=%dms)",
                activity.scope_id,
                decision.idle_duration_ms,
            )
            return

        # Mark as warned
        await self._activity_store.mark_warned(activity.scope_id)

        # Invoke callback if configured
        if self._warning_callback and self._config.thresholds.warn_callback:
            try:
                await self._warning_callback.on_idle_warning(
                    activity.scope_id,
                    decision.idle_duration_ms,
                    self._config.thresholds.hard_idle_ms,
                )
            except Exception as e:
                _log.warning(
                    "warning callback failed for %s: %s",
                    activity.scope_id,
                    e,
                )

        _log.debug(
            "issued idle warning for %s (idle=%dms, hard=%dms)",
            activity.scope_id,
            decision.idle_duration_ms,
            self._config.thresholds.hard_idle_ms,
        )

    async def record_activity(
        self,
        scope_id: str,
        sandbox_id: str | None = None,
    ) -> None:
        """Record activity for a scope."""
        await self._activity_store.record_activity(scope_id, sandbox_id)


# =============================================================================
# Factory
# =============================================================================


def create_idle_reaper(
    *,
    mode: IdleReapMode = IdleReapMode.PRODUCTION,
    activity_store: ActivityStore,
    terminator: SandboxTerminator,
    snapshot_creator: SnapshotCreator | None = None,
    warning_callback: IdleWarningCallback | None = None,
    thresholds: IdleThresholds | None = None,
    dry_run: bool = False,
) -> IdleReaper:
    """Create an idle reaper with specified mode.

    Factory function that matches QM's idle reaping pattern.

    Args:
        mode: Operational mode (production or development).
        activity_store: Store for activity tracking.
        terminator: Protocol for terminating sandboxes.
        snapshot_creator: Optional protocol for snapshots.
        warning_callback: Optional callback for soft idle warnings.
        thresholds: Custom thresholds. If None, uses mode defaults.
        dry_run: If True, log but don't actually reap.

    Returns:
        IdleReaper instance ready for reaping operations.

    Example:
        >>> reaper = create_idle_reaper(
        ...     mode=IdleReapMode.PRODUCTION,
        ...     activity_store=my_store,
        ...     terminator=my_terminator,
        ...     snapshot_creator=my_snapshot_creator,
        ... )
        >>> stats = await reaper.reap_idle_sandboxes()
        >>> print(f"Reaped {stats.sandboxes_reaped} sandboxes")
    """
    if thresholds is None:
        thresholds = IdleThresholds.from_mode(mode)

    config = ReapConfig(
        thresholds=thresholds,
        dry_run=dry_run,
    )

    return ConfigurableIdleReaper(
        config=config,
        activity_store=activity_store,
        terminator=terminator,
        snapshot_creator=snapshot_creator,
        warning_callback=warning_callback,
    )
