"""Stall guard — enhanced 3-signal stall detection (QM pattern).

Detects stalled operations via three complementary signals:
1. TIME: Total run time budget exceeded (default: 5 minutes)
2. ROUNDS: Too many consecutive stalled rounds (default: 5 rounds, auto-waive)
3. HEARTBEAT: No heartbeat received (default: 3 minutes)

Plus QUIET detection: heartbeat ok but no meaningful progress.

Reference: QM's 3-signal stall detection system.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Final

from supervisor.policy.types import StallSignal, StallState


__all__ = [
    "StallGuard",
    "EnhancedStallGuard",
    "DEFAULT_RUN_STALL_BUDGET_MS",
    "DEFAULT_STALL_ROUND_LIMIT",
    "DEFAULT_HEARTBEAT_INTERVAL_MS",
    "DEFAULT_QUIET_THRESHOLD_MS",
]


# =============================================================================
# QM Constants (from exploration)
# =============================================================================

# Total time budget before session is considered stalled
DEFAULT_RUN_STALL_BUDGET_MS: Final[int] = 300_000  # 5 minutes

# Number of stalled rounds before auto-waive
DEFAULT_STALL_ROUND_LIMIT: Final[int] = 5

# Heartbeat interval - no heartbeat triggers stall
DEFAULT_HEARTBEAT_INTERVAL_MS: Final[int] = 180_000  # 3 minutes

# Quiet threshold - heartbeat ok but no progress
DEFAULT_QUIET_THRESHOLD_MS: Final[int] = 60_000  # 1 minute


def _now_ms() -> int:
    """Current time in milliseconds."""
    return int(time.time() * 1000)


# =============================================================================
# Internal State
# =============================================================================

@dataclass
class _SessionState:
    """Mutable internal state for a session."""
    start_ms: int
    last_heartbeat_ms: int
    last_progress_ms: int
    stalled_rounds: int
    waived: bool


# =============================================================================
# Enhanced Stall Guard (3-signal)
# =============================================================================

class EnhancedStallGuard:
    """3-signal stall detection matching QM's approach.

    Combines:
    1. Time-based: Total session runtime budget
    2. Round-based: Consecutive stalled rounds counter
    3. Heartbeat-based: Regular heartbeat monitoring

    With auto-waive after N stalled rounds to prevent infinite loops.

    Example:
        guard = EnhancedStallGuard()

        # Start tracking session
        guard.start_session("sess_123")

        # Record heartbeats periodically
        guard.heartbeat("sess_123")

        # Record meaningful progress (tool completion, etc)
        guard.record_progress("sess_123")

        # Record when a round makes no progress
        guard.record_stalled_round("sess_123")

        # Check for stall
        state = guard.check("sess_123")
        if state.stalled:
            if state.auto_waived:
                logger.info("Auto-waived after 5 stalled rounds")
            else:
                logger.warning(f"Session stalled: {state.signal}")
    """

    __slots__ = (
        "_states",
        "_stall_budget_ms",
        "_round_limit",
        "_heartbeat_ms",
        "_quiet_ms",
    )

    def __init__(
        self,
        *,
        stall_budget_ms: int = DEFAULT_RUN_STALL_BUDGET_MS,
        round_limit: int = DEFAULT_STALL_ROUND_LIMIT,
        heartbeat_ms: int = DEFAULT_HEARTBEAT_INTERVAL_MS,
        quiet_ms: int = DEFAULT_QUIET_THRESHOLD_MS,
    ) -> None:
        """Initialize enhanced stall guard.

        Args:
            stall_budget_ms: Total time budget before TIME stall (default: 5 min)
            round_limit: Stalled rounds before ROUNDS stall/auto-waive (default: 5)
            heartbeat_ms: Heartbeat interval for HEARTBEAT stall (default: 3 min)
            quiet_ms: No-progress threshold for QUIET detection (default: 1 min)
        """
        self._states: dict[str, _SessionState] = {}
        self._stall_budget_ms = stall_budget_ms
        self._round_limit = round_limit
        self._heartbeat_ms = heartbeat_ms
        self._quiet_ms = quiet_ms

    def start_session(self, session_id: str) -> None:
        """Initialize tracking for a new session.

        Args:
            session_id: Session to start tracking
        """
        now = _now_ms()
        self._states[session_id] = _SessionState(
            start_ms=now,
            last_heartbeat_ms=now,
            last_progress_ms=now,
            stalled_rounds=0,
            waived=False,
        )

    def heartbeat(self, session_id: str) -> None:
        """Record heartbeat from session.

        Should be called periodically (every ~1 minute) to indicate
        the session is still active.

        Args:
            session_id: Session sending heartbeat
        """
        if session_id in self._states:
            self._states[session_id].last_heartbeat_ms = _now_ms()

    def record_progress(self, session_id: str) -> None:
        """Record meaningful progress (tool completion, new turn, etc).

        Resets stalled rounds counter and updates progress timestamp.

        Args:
            session_id: Session making progress
        """
        if session_id in self._states:
            now = _now_ms()
            state = self._states[session_id]
            state.last_progress_ms = now
            state.last_heartbeat_ms = now  # Progress counts as heartbeat
            state.stalled_rounds = 0  # Reset on progress

    def record_stalled_round(self, session_id: str) -> None:
        """Record a round that made no meaningful progress.

        Increments the stalled rounds counter.

        Args:
            session_id: Session with stalled round
        """
        if session_id in self._states:
            self._states[session_id].stalled_rounds += 1

    def check(self, session_id: str) -> StallState:
        """Check all 3 stall signals for a session.

        Returns StallState indicating whether session is stalled
        and which signal triggered it.

        Args:
            session_id: Session to check

        Returns:
            StallState with stall status and signal details
        """
        state = self._states.get(session_id)

        # No state - return default (not stalled)
        if state is None:
            return StallState(
                session_id=session_id,
                stalled=False,
                signal=None,
                stalled_rounds=0,
                time_elapsed_ms=0,
                last_heartbeat_ms=0,
                last_progress_ms=0,
                auto_waived=False,
                is_stalled=False,
            )

        now = _now_ms()
        elapsed = now - state.start_ms
        since_heartbeat = now - state.last_heartbeat_ms
        since_progress = now - state.last_progress_ms

        # Check for auto-waive (already waived after N stalled rounds)
        if state.stalled_rounds >= self._round_limit:
            if not state.waived:
                state.waived = True
            return StallState(
                session_id=session_id,
                stalled=False,
                signal=None,
                stalled_rounds=state.stalled_rounds,
                time_elapsed_ms=elapsed,
                last_heartbeat_ms=state.last_heartbeat_ms,
                last_progress_ms=state.last_progress_ms,
                auto_waived=True,
                is_stalled=False,
            )

        # Signal 1: TIME - Total time budget exceeded
        if elapsed > self._stall_budget_ms:
            return StallState(
                session_id=session_id,
                stalled=True,
                signal=StallSignal.TIME,
                stalled_rounds=state.stalled_rounds,
                time_elapsed_ms=elapsed,
                last_heartbeat_ms=state.last_heartbeat_ms,
                last_progress_ms=state.last_progress_ms,
                auto_waived=False,
                is_stalled=True,
                stall_duration_ms=elapsed - self._stall_budget_ms,
            )

        # Signal 2: ROUNDS - Too many stalled rounds (before auto-waive)
        if state.stalled_rounds >= self._round_limit:
            return StallState(
                session_id=session_id,
                stalled=True,
                signal=StallSignal.ROUNDS,
                stalled_rounds=state.stalled_rounds,
                time_elapsed_ms=elapsed,
                last_heartbeat_ms=state.last_heartbeat_ms,
                last_progress_ms=state.last_progress_ms,
                auto_waived=False,
                is_stalled=True,
            )

        # Signal 3: HEARTBEAT - No heartbeat received
        if since_heartbeat > self._heartbeat_ms:
            return StallState(
                session_id=session_id,
                stalled=True,
                signal=StallSignal.HEARTBEAT,
                stalled_rounds=state.stalled_rounds,
                time_elapsed_ms=elapsed,
                last_heartbeat_ms=state.last_heartbeat_ms,
                last_progress_ms=state.last_progress_ms,
                auto_waived=False,
                is_stalled=True,
                stall_duration_ms=since_heartbeat - self._heartbeat_ms,
            )

        # Signal 4: QUIET - Heartbeat ok but no progress (not a stall, just quiet)
        if since_progress > self._quiet_ms and since_heartbeat < self._heartbeat_ms:
            return StallState(
                session_id=session_id,
                stalled=False,  # Not stalled, just quiet
                signal=StallSignal.QUIET,
                stalled_rounds=state.stalled_rounds,
                time_elapsed_ms=elapsed,
                last_heartbeat_ms=state.last_heartbeat_ms,
                last_progress_ms=state.last_progress_ms,
                auto_waived=False,
                is_stalled=False,
                quiet_since_ms=state.last_progress_ms + self._quiet_ms,
            )

        # All signals ok
        return StallState(
            session_id=session_id,
            stalled=False,
            signal=None,
            stalled_rounds=state.stalled_rounds,
            time_elapsed_ms=elapsed,
            last_heartbeat_ms=state.last_heartbeat_ms,
            last_progress_ms=state.last_progress_ms,
            auto_waived=False,
            is_stalled=False,
        )

    def reset(self, session_id: str) -> None:
        """Reset stall state by re-initializing session.

        Args:
            session_id: Session to reset
        """
        self.start_session(session_id)

    def clear(self, session_id: str) -> None:
        """Clear all state for a session.

        Call when session ends.

        Args:
            session_id: Session to clear
        """
        self._states.pop(session_id, None)

    def list_stalled(self) -> list[StallState]:
        """List all currently stalled sessions.

        Returns:
            List of StallState for stalled sessions
        """
        results = []
        for session_id in list(self._states.keys()):
            state = self.check(session_id)
            if state.stalled:
                results.append(state)
        return results

    def list_quiet(self) -> list[StallState]:
        """List sessions that are quiet but not stalled.

        Returns:
            List of StallState for quiet sessions
        """
        results = []
        for session_id in list(self._states.keys()):
            state = self.check(session_id)
            if state.signal == StallSignal.QUIET and not state.stalled:
                results.append(state)
        return results

    def list_auto_waived(self) -> list[StallState]:
        """List sessions that have been auto-waived.

        Returns:
            List of StallState for auto-waived sessions
        """
        results = []
        for session_id in list(self._states.keys()):
            state = self.check(session_id)
            if state.auto_waived:
                results.append(state)
        return results

    # Properties for configuration access
    @property
    def stall_budget_ms(self) -> int:
        """Total time budget before TIME stall."""
        return self._stall_budget_ms

    @property
    def round_limit(self) -> int:
        """Stalled rounds before ROUNDS stall/auto-waive."""
        return self._round_limit

    @property
    def heartbeat_ms(self) -> int:
        """Heartbeat interval for HEARTBEAT stall."""
        return self._heartbeat_ms

    @property
    def quiet_ms(self) -> int:
        """No-progress threshold for QUIET detection."""
        return self._quiet_ms


# =============================================================================
# Legacy Alias
# =============================================================================

# StallGuard is now an alias for EnhancedStallGuard
StallGuard = EnhancedStallGuard
