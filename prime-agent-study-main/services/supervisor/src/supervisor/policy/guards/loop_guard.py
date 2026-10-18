"""Loop guard — repetition and oscillation detection.

Rewritten with:
- Clean state management
- Configurable window sizes
- Uses fast hash from detectors module
- Clear state machine
- Proper reset on progress

Detects:
- Repetition: N consecutive identical tool calls
- Oscillation: A-B-A-B pattern

Reference: QM's loop detection pattern.
"""

from __future__ import annotations

from collections import deque
from typing import Any

from supervisor.policy.guards.detectors import call_hash
from supervisor.policy.types import GuardAction, GuardConfig, GuardDetector, GuardVerdict


__all__ = ["LoopGuard"]


class LoopGuard:
    """Sliding-window loop detection for tool calls.

    Maintains a window of recent call hashes and detects:
    - Repetition: N consecutive identical calls
    - Oscillation: A-B-A-B alternating pattern

    Thread-safe for concurrent sessions via per-session state.

    Example:
        guard = LoopGuard()

        # Configure for session
        guard.configure("sess_123", GuardConfig(
            repetition=GuardAction.ABORT,
            oscillation=GuardAction.WARN,
            stall_iterations=10,
        ))

        # On each tool call
        verdict = guard.observe(
            session_id="sess_123",
            tool_name="bash",
            args_hash=call_hash("bash", {"cmd": "ls"}),
        )
        if verdict.tripped:
            if verdict.action == GuardAction.ABORT:
                raise LoopDetected(...)
            elif verdict.action == GuardAction.WARN:
                logger.warning(verdict.detail)
    """

    __slots__ = ("_config", "_session_configs", "_hashes", "_stall_count")

    def __init__(
        self,
        *,
        default_config: GuardConfig | None = None,
    ) -> None:
        self._config = default_config or GuardConfig()
        self._session_configs: dict[str, GuardConfig] = {}
        # Per-session hash windows
        self._hashes: dict[str, deque[str]] = {}
        # Per-session stall counter
        self._stall_count: dict[str, int] = {}

    def configure(self, session_id: str, config: GuardConfig) -> None:
        """Configure guard for specific session.

        Args:
            session_id: Session to configure
            config: Guard configuration
        """
        self._session_configs[session_id] = config

    def observe(
        self,
        session_id: str,
        tool_name: str,
        args_hash: str,
    ) -> GuardVerdict:
        """Observe tool call and check for loops.

        Args:
            session_id: Session making call
            tool_name: Name of tool being called
            args_hash: Hash of tool arguments

        Returns:
            Verdict indicating if loop detected
        """
        config = self._session_configs.get(session_id, self._config)

        # Get or create hash window
        if session_id not in self._hashes:
            self._hashes[session_id] = deque(maxlen=config.window_size)

        window = self._hashes[session_id]

        # Build call hash including tool name
        full_hash = f"{tool_name}:{args_hash}"
        window.append(full_hash)

        # Increment stall counter
        stall_count = self._stall_count.get(session_id, 0) + 1
        self._stall_count[session_id] = stall_count

        # Check repetition: last N identical
        if config.repetition != GuardAction.OFF:
            threshold = config.repetition_threshold
            if len(window) >= threshold:
                recent = list(window)[-threshold:]
                if len(set(recent)) == 1:
                    return GuardVerdict(
                        tripped=True,
                        detector=GuardDetector.REPETITION,
                        action=config.repetition,
                        detail=(
                            f"repetition: {threshold} consecutive identical calls "
                            f"tool={tool_name!r} hash={args_hash}"
                        ),
                        call_hash=args_hash,
                        tool_name=tool_name,
                        call_count=stall_count,
                    )

        # Check oscillation: A-B-A-B pattern
        if config.oscillation != GuardAction.OFF:
            if len(window) >= 4:
                a, b, c, d = list(window)[-4:]
                if a == c and b == d and a != b:
                    return GuardVerdict(
                        tripped=True,
                        detector=GuardDetector.OSCILLATION,
                        action=config.oscillation,
                        detail=(
                            f"oscillation: A-B-A-B pattern detected "
                            f"tool={tool_name!r} hashes=({a},{b})"
                        ),
                        call_hash=args_hash,
                        tool_name=tool_name,
                        call_count=stall_count,
                    )

        # Check stall: too many calls without progress
        if config.stall_iterations > 0:
            if stall_count >= config.stall_iterations:
                return GuardVerdict(
                    tripped=True,
                    detector=GuardDetector.STALL,
                    action=GuardAction.ABORT,
                    detail=(
                        f"stall: no progress for {stall_count} tool iterations "
                        f"last_tool={tool_name!r}"
                    ),
                    call_hash=args_hash,
                    tool_name=tool_name,
                    call_count=stall_count,
                )

        return GuardVerdict(
            tripped=False,
            call_hash=args_hash,
            tool_name=tool_name,
            call_count=stall_count,
        )

    def reset(self, session_id: str) -> None:
        """Reset state for session on progress.

        Call when session makes meaningful progress:
        - Message completion
        - Run completion
        - User interaction

        Args:
            session_id: Session to reset
        """
        self._stall_count[session_id] = 0

    def clear(self, session_id: str) -> None:
        """Clear all state for session.

        Call when session ends.

        Args:
            session_id: Session to clear
        """
        self._session_configs.pop(session_id, None)
        self._hashes.pop(session_id, None)
        self._stall_count.pop(session_id, None)

    def window_size(self, session_id: str) -> int:
        """Get current window size for session.

        Args:
            session_id: Session to check

        Returns:
            Number of calls in window
        """
        return len(self._hashes.get(session_id, []))

    def stall_count(self, session_id: str) -> int:
        """Get current stall count for session.

        Args:
            session_id: Session to check

        Returns:
            Number of calls since last reset
        """
        return self._stall_count.get(session_id, 0)
