"""Guards subsystem — loop and stall detection.

Components:
- LoopGuard: Repetition and oscillation detection
- StallGuard/EnhancedStallGuard: 3-signal stall detection (QM pattern)
- Detectors: Hash-based detection utilities
- GuardAction, GuardConfig: Type definitions
"""

from supervisor.policy.guards.loop_guard import LoopGuard
from supervisor.policy.guards.stall_guard import (
    StallGuard,
    EnhancedStallGuard,
    DEFAULT_RUN_STALL_BUDGET_MS,
    DEFAULT_STALL_ROUND_LIMIT,
    DEFAULT_HEARTBEAT_INTERVAL_MS,
    DEFAULT_QUIET_THRESHOLD_MS,
)
from supervisor.policy.guards.detectors import call_hash, canonicalize_args
from supervisor.policy.types import GuardAction, GuardConfig

__all__ = [
    "GuardAction",
    "GuardConfig",
    "LoopGuard",
    "StallGuard",
    "EnhancedStallGuard",
    "DEFAULT_RUN_STALL_BUDGET_MS",
    "DEFAULT_STALL_ROUND_LIMIT",
    "DEFAULT_HEARTBEAT_INTERVAL_MS",
    "DEFAULT_QUIET_THRESHOLD_MS",
    "call_hash",
    "canonicalize_args",
]
