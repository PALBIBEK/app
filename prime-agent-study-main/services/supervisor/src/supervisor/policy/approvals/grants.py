"""Approval grants — QM-style once/session/always approval bypasses.

Grants allow users to pre-authorize certain command patterns:
- ONCE: Single use, auto-consumed on match
- SESSION: Valid for session lifetime
- ALWAYS: Persisted until explicit revoke

Reference: QM's approval grant system.
"""

from __future__ import annotations

import time

__all__ = [
]


def _now_ms() -> int:
    """Current time in milliseconds."""
    return int(time.time() * 1000)


