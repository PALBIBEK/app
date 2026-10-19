"""Sandbox migration between backends.

This module handles migrating scopes from one backend to another:
- Validates migration safety (pinned? live work? capabilities?)
- Copies workspace from source to destination
- Updates route in database
- Optionally resyncs if source changed during copy

Migration flow:
1. Validate: Check pinned, live work, capability loss
2. Lock: Acquire advisory lock for scope
3. Copy: Transfer workspace via sync or tar
4. Route: Update database route
5. (Optional) Resync: Re-copy if source changed

Reference: QM's sandbox-migration-runner.ts pattern.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

__all__ = [
    "MigrationResult",
]


@dataclass(frozen=True, slots=True)
class MigrationResult:
    """Result of a scope migration."""

    success: bool
    scope_id: str
    from_backend: str
    to_backend: str
    migration_sha: str | None = None
    capabilities_lost: tuple[str, ...] = ()
    error: str | None = None
    duration_ms: int = 0

    def to_dict(self) -> dict:
        """Convert to dict for serialization."""
        return {
            "success": self.success,
            "scope_id": self.scope_id,
            "from_backend": self.from_backend,
            "to_backend": self.to_backend,
            "migration_sha": self.migration_sha,
            "capabilities_lost": list(self.capabilities_lost),
            "error": self.error,
            "duration_ms": self.duration_ms,
        }


LiveWorkChecker = Callable[[str], Awaitable[bool]]


