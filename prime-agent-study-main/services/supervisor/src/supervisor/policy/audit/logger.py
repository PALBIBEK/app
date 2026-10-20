"""Audit logger — policy decision logging with deduplication.

Provides audit trail for:
- Approval requests/responses
- Budget exceeded events
- Quota exceeded events
- Rate limiting events
- Command blocks
- Loop detection

Reference: QM's audit logging pattern.
"""

from __future__ import annotations

__all__ = [
]


# Default max events to keep in memory
DEFAULT_MAX_EVENTS = 50_000

# Default deduplication window (5 minutes)
DEFAULT_DEDUP_WINDOW_MS = 300_000


