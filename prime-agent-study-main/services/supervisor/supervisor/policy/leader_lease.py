"""Leader lease — distributed coordination for single-writer patterns.

Ensures only one replica runs certain operations:
- Approval expiration sweeper
- Stale session cleanup
- Scheduled tasks

Uses:
- Postgres advisory locks for cross-replica coordination
- asyncio.Lock for in-process serialization

Rewritten with:
- Configurable lock keys
- Timeout support
- Metrics hooks
- Better error handling

Reference: D8/D12 multi-replica patterns.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Final, TypeVar

__all__ = [
    "APPROVAL_SWEEPER_LOCK_KEY",
    "SCHEDULED_TASK_LOCK_KEY",
    "STALE_SESSION_LOCK_KEY",
]


logger = logging.getLogger(__name__)

T = TypeVar("T")


# =============================================================================
# Lock Keys (stable identifiers for advisory locks)
# =============================================================================

# Approval sweeper (D12)
APPROVAL_SWEEPER_LOCK_KEY: Final[int] = 0xA12_0D12

# Stale session cleanup
STALE_SESSION_LOCK_KEY: Final[int] = 0xA12_0D13

# Scheduled task runner
SCHEDULED_TASK_LOCK_KEY: Final[int] = 0xA12_0D14


# =============================================================================
# Process-local Locks
# =============================================================================

# In-process locks for local serialization (SQLite, dual-manager tests)
_local_locks: dict[int, asyncio.Lock] = {}


# =============================================================================
# Leader Lease Class
# =============================================================================

# =============================================================================
# Convenience Functions (backward compatibility)
# =============================================================================

