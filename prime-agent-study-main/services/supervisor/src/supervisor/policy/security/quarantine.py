"""Quarantine store — blocked content storage for review.

Stores blocked content metadata for admin review:
- Content hash (not raw content for security)
- Preview (truncated/redacted)
- Verdict details
- Review status

Reference: QM's quarantine pattern.
"""

from __future__ import annotations

import logging
import time
from typing import Final

__all__ = [
    "DEFAULT_MAX_QUARANTINE_SIZE",
]


_log = logging.getLogger(__name__)


# Default max items in quarantine
DEFAULT_MAX_QUARANTINE_SIZE: Final[int] = 10_000


def _now_ms() -> int:
    """Current time in milliseconds."""
    return int(time.time() * 1000)


# =============================================================================
# In-Memory Quarantine Store
# =============================================================================

# =============================================================================
# Unified Quarantine Store
# =============================================================================

