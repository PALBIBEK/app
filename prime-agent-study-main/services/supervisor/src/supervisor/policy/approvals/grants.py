"""Approval grants — QM-style once/session/always approval bypasses.

Grants allow users to pre-authorize certain command patterns:
- ONCE: Single use, auto-consumed on match
- SESSION: Valid for session lifetime
- ALWAYS: Persisted until explicit revoke

Reference: QM's approval grant system.
"""

from __future__ import annotations

import fnmatch
import re
import time
from typing import TYPE_CHECKING
from uuid import uuid4

from supervisor.policy.types import ApprovalGrant, ApprovalScope

if TYPE_CHECKING:
    pass


__all__ = ["ApprovalGrantStore"]


def _now_ms() -> int:
    """Current time in milliseconds."""
    return int(time.time() * 1000)


class ApprovalGrantStore:
    """Manages approval grants with in-memory storage.

    Grants bypass the normal approval flow for matching commands.
    Pattern matching supports:
    - Exact match: "git push"
    - Glob patterns: "git *"
    - Regex patterns (prefix with "re:"): "re:rm -rf .*"

    Thread-safe for concurrent access via atomic operations.

    Example:
        store = ApprovalGrantStore()

        # Grant session-scoped approval for git commands
        grant = await store.grant(
            principal_id="user_123",
            command_pattern="git *",
            scope=ApprovalScope.SESSION,
            session_id="sess_456",
        )

        # Check if command has grant
        match = await store.check_grant(
            principal_id="user_123",
            command="git push origin main",
            session_id="sess_456",
        )
        if match:
            # Skip approval, use grant
            ...
    """

    __slots__ = ("_grants", "_session_grants", "_always_grants")

    def __init__(self) -> None:
        # All grants by ID
        self._grants: dict[str, ApprovalGrant] = {}
        # Session-scoped grants: {session_id: {grant_id}}
        self._session_grants: dict[str, set[str]] = {}
        # Always-scoped grants: {principal_id: {grant_id}}
        self._always_grants: dict[str, set[str]] = {}

    async def grant(
        self,
        principal_id: str,
        command_pattern: str,
        scope: ApprovalScope,
        *,
        session_id: str | None = None,
        expires_at_ms: int | None = None,
    ) -> ApprovalGrant:
        """Create new approval grant.

        Args:
            principal_id: User/principal creating grant
            command_pattern: Pattern to match (exact, glob, or re:regex)
            scope: ONCE, SESSION, or ALWAYS
            session_id: Required for SESSION scope
            expires_at_ms: Optional expiration timestamp

        Returns:
            Created grant

        Raises:
            ValueError: SESSION scope without session_id
        """
        if scope == ApprovalScope.SESSION and session_id is None:
            raise ValueError("session_id required for SESSION scope")

        grant_id = uuid4().hex
        now_ms = _now_ms()

        grant = ApprovalGrant(
            grant_id=grant_id,
            principal_id=principal_id,
            command_pattern=command_pattern,
            scope=scope,
            session_id=session_id if scope == ApprovalScope.SESSION else None,
            created_at_ms=now_ms,
            expires_at_ms=expires_at_ms,
        )

        self._grants[grant_id] = grant

        # Index by scope
        if scope == ApprovalScope.SESSION:
            session_grants = self._session_grants.setdefault(session_id, set())  # type: ignore
            session_grants.add(grant_id)
        elif scope == ApprovalScope.ALWAYS:
            always_grants = self._always_grants.setdefault(principal_id, set())
            always_grants.add(grant_id)

        return grant

    async def check_grant(
        self,
        principal_id: str,
        command: str,
        session_id: str,
    ) -> ApprovalGrant | None:
        """Check if command has matching grant.

        Checks in order:
        1. ONCE grants (consumed on match)
        2. SESSION grants
        3. ALWAYS grants

        Args:
            principal_id: User executing command
            command: Command to check
            session_id: Current session

        Returns:
            Matching grant or None
        """
        now_ms = _now_ms()

        # Check session grants first (most specific)
        session_grants = self._session_grants.get(session_id, set())
        for grant_id in list(session_grants):
            grant = self._grants.get(grant_id)
            if grant is None:
                continue
            if grant.principal_id != principal_id:
                continue
            if self._is_expired(grant, now_ms):
                await self._remove_grant(grant_id)
                continue
            if self._matches(grant.command_pattern, command):
                if grant.scope == ApprovalScope.ONCE:
                    await self._remove_grant(grant_id)
                return grant

        # Check always grants
        always_grants = self._always_grants.get(principal_id, set())
        for grant_id in list(always_grants):
            grant = self._grants.get(grant_id)
            if grant is None:
                continue
            if self._is_expired(grant, now_ms):
                await self._remove_grant(grant_id)
                continue
            if self._matches(grant.command_pattern, command):
                if grant.scope == ApprovalScope.ONCE:
                    await self._remove_grant(grant_id)
                return grant

        return None

    async def revoke(self, grant_id: str) -> bool:
        """Revoke a grant.

        Args:
            grant_id: Grant to revoke

        Returns:
            True if grant was found and revoked
        """
        return await self._remove_grant(grant_id)

    async def list_grants(
        self,
        principal_id: str,
        *,
        session_id: str | None = None,
        limit: int = 100,
    ) -> list[ApprovalGrant]:
        """List grants for principal.

        Args:
            principal_id: Principal to list grants for
            session_id: Optional filter by session
            limit: Max grants to return

        Returns:
            List of grants
        """
        now_ms = _now_ms()
        results: list[ApprovalGrant] = []

        for grant in self._grants.values():
            if grant.principal_id != principal_id:
                continue
            if session_id and grant.session_id and grant.session_id != session_id:
                continue
            if self._is_expired(grant, now_ms):
                continue
            results.append(grant)
            if len(results) >= limit:
                break

        # Sort by created_at descending
        results.sort(key=lambda g: g.created_at_ms, reverse=True)
        return results[:limit]

    async def clear_session(self, session_id: str) -> int:
        """Clear all grants for a session.

        Args:
            session_id: Session to clear

        Returns:
            Number of grants cleared
        """
        grant_ids = list(self._session_grants.get(session_id, set()))
        count = 0
        for grant_id in grant_ids:
            if await self._remove_grant(grant_id):
                count += 1
        return count

    async def _remove_grant(self, grant_id: str) -> bool:
        """Remove grant from all indexes."""
        grant = self._grants.pop(grant_id, None)
        if grant is None:
            return False

        if grant.session_id:
            session_grants = self._session_grants.get(grant.session_id)
            if session_grants:
                session_grants.discard(grant_id)

        always_grants = self._always_grants.get(grant.principal_id)
        if always_grants:
            always_grants.discard(grant_id)

        return True

    def _is_expired(self, grant: ApprovalGrant, now_ms: int) -> bool:
        """Check if grant is expired."""
        if grant.expires_at_ms is None:
            return False
        return now_ms > grant.expires_at_ms

    def _matches(self, pattern: str, command: str) -> bool:
        """Check if command matches pattern.

        Pattern types:
        - Exact: "git push"
        - Glob: "git *" (fnmatch)
        - Regex: "re:rm -rf .*"
        """
        # Regex pattern
        if pattern.startswith("re:"):
            try:
                regex = pattern[3:]
                return bool(re.match(regex, command))
            except re.error:
                return False

        # Glob pattern (has wildcards)
        if "*" in pattern or "?" in pattern or "[" in pattern:
            return fnmatch.fnmatch(command, pattern)

        # Exact match
        return command == pattern
