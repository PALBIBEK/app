"""Computer status and restart operations.

This module provides operations for checking sandbox computer health
and restarting sandboxes when needed.

Design (matches QM's computerStatus/restartComputer):
    1. Health check via guest probe
    2. Machine state detection
    3. Restart with boot ID tracking

Security:
    - Restart operations are rate-limited
    - Boot ID tracking detects VM restarts

Reference:
    - QM: sandbox.ts computerStatus, restartComputer
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum, auto
from typing import Final

_log = logging.getLogger(__name__)

__all__ = [
    "MachineState",
    "ComputerStatus",
    "RestartResult",
    "ComputerStatusError",
    "STATUS_PROBE_TIMEOUT_S",
    "RESTART_TIMEOUT_S",
]


# =============================================================================
# Constants
# =============================================================================

STATUS_PROBE_TIMEOUT_S: Final[float] = 15.0
RESTART_TIMEOUT_S: Final[float] = 60.0


# =============================================================================
# Enums
# =============================================================================


class MachineState(StrEnum):
    """Computer machine states."""

    RUNNING = auto()
    STOPPED = auto()
    PAUSED = auto()
    STARTING = auto()
    STOPPING = auto()
    UNKNOWN = auto()


# =============================================================================
# Errors
# =============================================================================


class ComputerStatusError(Exception):
    """Failed to get computer status or perform restart.

    Attributes:
        message: Error description.
        scope_id: Scope identifier, if known.
        retryable: Whether the operation can be retried.
    """

    def __init__(
        self,
        message: str,
        *,
        scope_id: str | None = None,
        retryable: bool = True,
    ) -> None:
        super().__init__(message)
        self.scope_id = scope_id
        self.retryable = retryable


# =============================================================================
# Data Classes
# =============================================================================


@dataclass(frozen=True, slots=True)
class ComputerStatus:
    """Status of a sandbox computer.

    Attributes:
        scope_id: Scope identifier.
        machine_state: Current machine state.
        guest_responsive: Whether guest agent is responding.
        uptime_ms: Uptime in milliseconds, if known.
        last_heartbeat_ms: Last heartbeat timestamp in milliseconds.
        boot_id: Current boot ID, if available.
        message: Human-readable status message.
    """

    scope_id: str
    machine_state: MachineState
    guest_responsive: bool
    uptime_ms: int | None = None
    last_heartbeat_ms: int | None = None
    boot_id: str | None = None
    message: str = ""

    @property
    def is_healthy(self) -> bool:
        """True if machine is running and guest is responsive."""
        return self.machine_state == MachineState.RUNNING and self.guest_responsive


@dataclass(frozen=True, slots=True)
class RestartResult:
    """Result of a computer restart operation.

    Attributes:
        scope_id: Scope that was restarted.
        previous_state: State before restart.
        new_state: State after restart.
        duration_ms: Time taken to restart in milliseconds.
        boot_id: New boot ID after restart, if available.
    """

    scope_id: str
    previous_state: MachineState
    new_state: MachineState
    duration_ms: int
    boot_id: str | None = None


# =============================================================================
# Abstract Base
# =============================================================================


class ComputerStatusOps(ABC):
    """Abstract base for computer status operations.

    Implementations check sandbox health and perform restarts.
    """

    __slots__ = ()

    @abstractmethod
    async def computer_status(
        self,
        scope_id: str,
        *,
        probe_timeout: float | None = None,
    ) -> ComputerStatus:
        """Get status of a sandbox computer.

        Performs health check via guest probe.

        Args:
            scope_id: Scope identifier.
            probe_timeout: Timeout for guest probe in seconds.

        Returns:
            ComputerStatus with machine state and responsiveness.

        Raises:
            ComputerStatusError: If status check fails.
        """
        ...

    @abstractmethod
    async def restart_computer(
        self,
        scope_id: str,
        *,
        timeout: float | None = None,
    ) -> RestartResult:
        """Restart a sandbox computer.

        Args:
            scope_id: Scope to restart.
            timeout: Maximum wait time for restart in seconds.

        Returns:
            RestartResult with timing details.

        Raises:
            ComputerStatusError: If restart fails.
        """
        ...
