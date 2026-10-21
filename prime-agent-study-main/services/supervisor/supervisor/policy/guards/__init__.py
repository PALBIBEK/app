"""Guards subsystem — loop detection.

Components:
- LoopGuard: Repetition and oscillation detection
- Detectors: Hash-based detection utilities
- GuardAction, GuardConfig: Type definitions
"""

from supervisor.policy.guards.detectors import call_hash, canonicalize_args
from supervisor.policy.guards.loop_guard import LoopGuard
from supervisor.policy.types import GuardAction, GuardConfig

__all__ = [
    "GuardAction",
    "GuardConfig",
    "LoopGuard",
    "call_hash",
    "canonicalize_args",
]
