"""Quarantine store — blocked content storage for review.

Stores blocked content metadata for admin review:
- Content hash (not raw content for security)
- Preview (truncated/redacted)
- Verdict details
- Review status

Reference: QM's quarantine pattern.
"""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from collections import OrderedDict
from typing import Final

from supervisor.policy.security.types import (
    DEFAULT_QUARANTINE_RETENTION_MS,
    ContentType,
    QuarantinedContent,
    SecurityVerdict,
)

__all__ = [
    "DEFAULT_MAX_QUARANTINE_SIZE",
    "InMemoryQuarantineStore",
    "QuarantineStore",
]


_log = logging.getLogger(__name__)


# Default max items in quarantine
DEFAULT_MAX_QUARANTINE_SIZE: Final[int] = 10_000


def _now_ms() -> int:
    """Current time in milliseconds."""
    return int(time.time() * 1000)


def _hash_content(content: str) -> str:
    """Hash content for storage (don't store raw)."""
    return hashlib.sha256(content.encode()).hexdigest()


def _truncate_preview(content: str, max_len: int = 100) -> str:
    """Create safe preview of content."""
    if len(content) <= max_len:
        return content
    return content[:max_len] + "..."


def _generate_quarantine_id() -> str:
    """Generate unique quarantine ID."""
    return f"quar_{uuid.uuid4().hex[:16]}"


# =============================================================================
# In-Memory Quarantine Store
# =============================================================================

class InMemoryQuarantineStore:
    """In-memory quarantine store for development/testing.

    Features:
    - Bounded memory (max_size)
    - Automatic cleanup of old entries
    - Query support

    Example:
        store = InMemoryQuarantineStore()

        # Quarantine blocked content
        quarantine_id = await store.quarantine(
            content="rm -rf /",
            verdict=verdict,
            session_id="sess_123",
        )

        # List pending reviews
        pending = await store.list_pending()

        # Mark as reviewed
        await store.mark_reviewed(quarantine_id, "confirmed_threat", "admin_1")
    """

    __slots__ = ("_items", "_max_size", "_retention_ms")

    def __init__(
        self,
        *,
        max_size: int = DEFAULT_MAX_QUARANTINE_SIZE,
        retention_ms: int = DEFAULT_QUARANTINE_RETENTION_MS,
    ) -> None:
        """Initialize in-memory quarantine store.

        Args:
            max_size: Maximum items to store
            retention_ms: How long to retain items
        """
        self._items: OrderedDict[str, QuarantinedContent] = OrderedDict()
        self._max_size = max_size
        self._retention_ms = retention_ms

    async def quarantine(
        self,
        content: str,
        verdict: SecurityVerdict,
        session_id: str,
        *,
        content_type: ContentType = ContentType.COMMAND,
        principal_id: str | None = None,
    ) -> str:
        """Quarantine blocked content.

        Args:
            content: Content to quarantine
            verdict: Blocking verdict
            session_id: Session that triggered
            content_type: Type of content
            principal_id: User/principal

        Returns:
            Quarantine ID
        """
        # Clean old entries first
        self._cleanup()

        quarantine_id = _generate_quarantine_id()

        item = QuarantinedContent.create(
            quarantine_id=quarantine_id,
            content_hash=_hash_content(content),
            content_preview=_truncate_preview(content),
            content_type=content_type,
            verdict=verdict,
            session_id=session_id,
            principal_id=principal_id,
        )

        self._items[quarantine_id] = item

        # Enforce max size
        while len(self._items) > self._max_size:
            self._items.popitem(last=False)

        return quarantine_id

    async def get(self, quarantine_id: str) -> QuarantinedContent | None:
        """Get quarantined item by ID.

        Args:
            quarantine_id: Item ID

        Returns:
            Quarantined content or None
        """
        return self._items.get(quarantine_id)

    async def list_pending(
        self,
        *,
        session_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[QuarantinedContent]:
        """List items pending review.

        Args:
            session_id: Filter by session
            limit: Max items to return
            offset: Skip first N items

        Returns:
            List of pending items
        """
        results = []
        skipped = 0

        for item in reversed(self._items.values()):
            if item.reviewed:
                continue
            if session_id and item.session_id != session_id:
                continue

            if skipped < offset:
                skipped += 1
                continue

            results.append(item)

            if len(results) >= limit:
                break

        return results

    async def list_reviewed(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[QuarantinedContent]:
        """List reviewed items.

        Args:
            limit: Max items to return
            offset: Skip first N items

        Returns:
            List of reviewed items
        """
        results = []
        skipped = 0

        for item in reversed(self._items.values()):
            if not item.reviewed:
                continue

            if skipped < offset:
                skipped += 1
                continue

            results.append(item)

            if len(results) >= limit:
                break

        return results

    async def mark_reviewed(
        self,
        quarantine_id: str,
        action: str,
        reviewed_by: str,
    ) -> bool:
        """Mark item as reviewed.

        Args:
            quarantine_id: Item to mark
            action: Review action (allow/block/delete)
            reviewed_by: Who reviewed

        Returns:
            True if found and updated
        """
        item = self._items.get(quarantine_id)
        if not item:
            return False

        # Create updated item (frozen dataclass)
        updated = QuarantinedContent(
            quarantine_id=item.quarantine_id,
            content_hash=item.content_hash,
            content_preview=item.content_preview,
            content_type=item.content_type,
            verdict=item.verdict,
            session_id=item.session_id,
            principal_id=item.principal_id,
            timestamp_ms=item.timestamp_ms,
            reviewed=True,
            review_action=action,
            review_by=reviewed_by,
            review_at_ms=_now_ms(),
        )

        self._items[quarantine_id] = updated
        return True

    async def delete(self, quarantine_id: str) -> bool:
        """Delete quarantined item.

        Args:
            quarantine_id: Item to delete

        Returns:
            True if found and deleted
        """
        if quarantine_id in self._items:
            del self._items[quarantine_id]
            return True
        return False

    async def count_pending(self) -> int:
        """Count items pending review."""
        return sum(1 for item in self._items.values() if not item.reviewed)

    async def count_total(self) -> int:
        """Count total items."""
        return len(self._items)

    def _cleanup(self) -> int:
        """Remove expired items.

        Returns:
            Number of items removed
        """
        cutoff = _now_ms() - self._retention_ms
        expired = [
            qid for qid, item in self._items.items()
            if item.timestamp_ms < cutoff
        ]

        for qid in expired:
            del self._items[qid]

        return len(expired)


# =============================================================================
# Unified Quarantine Store
# =============================================================================

class QuarantineStore:
    """Quarantine store with optional DB persistence.

    Uses in-memory storage with optional DB backing.
    For now, memory-only (DB persistence can be added later).

    Example:
        store = QuarantineStore()
        quarantine_id = await store.quarantine(content, verdict, session_id)
    """

    __slots__ = ("_memory",)

    def __init__(
        self,
        *,
        max_size: int = DEFAULT_MAX_QUARANTINE_SIZE,
        retention_ms: int = DEFAULT_QUARANTINE_RETENTION_MS,
    ) -> None:
        """Initialize quarantine store.

        Args:
            max_size: Maximum items to store
            retention_ms: How long to retain items
        """
        self._memory = InMemoryQuarantineStore(
            max_size=max_size,
            retention_ms=retention_ms,
        )

    async def quarantine(
        self,
        content: str,
        verdict: SecurityVerdict,
        session_id: str,
        *,
        content_type: ContentType = ContentType.COMMAND,
        principal_id: str | None = None,
    ) -> str:
        """Quarantine blocked content."""
        return await self._memory.quarantine(
            content,
            verdict,
            session_id,
            content_type=content_type,
            principal_id=principal_id,
        )

    async def get(self, quarantine_id: str) -> QuarantinedContent | None:
        """Get quarantined item by ID."""
        return await self._memory.get(quarantine_id)

    async def list_pending(
        self,
        *,
        session_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[QuarantinedContent]:
        """List items pending review."""
        return await self._memory.list_pending(
            session_id=session_id,
            limit=limit,
            offset=offset,
        )

    async def list_reviewed(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[QuarantinedContent]:
        """List reviewed items."""
        return await self._memory.list_reviewed(limit=limit, offset=offset)

    async def mark_reviewed(
        self,
        quarantine_id: str,
        action: str,
        reviewed_by: str,
    ) -> bool:
        """Mark item as reviewed."""
        return await self._memory.mark_reviewed(quarantine_id, action, reviewed_by)

    async def delete(self, quarantine_id: str) -> bool:
        """Delete quarantined item."""
        return await self._memory.delete(quarantine_id)

    async def count_pending(self) -> int:
        """Count items pending review."""
        return await self._memory.count_pending()

    async def count_total(self) -> int:
        """Count total items."""
        return await self._memory.count_total()
