"""In-memory repository implementations for development and testing.

Design principles:
- Full Protocol compliance
- Thread-safe operations (asyncio-safe)
- No external dependencies
- Fast iteration for tests
- Complete feature parity with Postgres

Reference: Refactored from store.py with Protocol compliance.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from supervisor.catalog.storage.converters import kind_of, with_digest, with_metadata, with_status
from supervisor.catalog.core.digests import content_digest
from supervisor.catalog.core.errors import (
    DigestMismatch,
    DigestMissing,
    FloatingRef,
    ImmutableArtifact,
    InvalidDigest,
    NotOffered,
)
from supervisor.catalog.core.types import (
    ArtifactKind,
    ArtifactMetadata,
    ArtifactStatus,
    ArtifactUsageRecord,
    ArtifactUsageStats,
)

if TYPE_CHECKING:
    from supervisor.catalog.core.types import Artifact


__all__ = [
    "InMemoryArtifactStore",
    "InMemoryUsageStore",
    "reject_floating_ref",
    "validate_digest_format",
]


# =============================================================================
# Validation Helpers
# =============================================================================

def reject_floating_ref(ref: str) -> None:
    """Refuse mutable tags like 'latest'.

    Args:
        ref: Reference string to validate

    Raises:
        FloatingRef: If reference is floating/mutable
    """
    s = ref.strip()
    if not s:
        raise FloatingRef("", "empty composition reference")
    lower = s.lower()
    if lower == "latest" or lower.endswith(":latest") or lower.endswith("@latest"):
        raise FloatingRef(ref, "exact digest or id required")
    if s.startswith("sha256:") and len(s) != len("sha256:") + 64:
        raise FloatingRef(ref, "malformed digest format")


def validate_digest_format(digest: str) -> None:
    """Validate digest format.

    Args:
        digest: Digest string to validate

    Raises:
        InvalidDigest: If digest format is invalid
    """
    if not digest:
        raise InvalidDigest("", "empty digest")
    if not digest.startswith("sha256:"):
        raise InvalidDigest(digest, "must start with 'sha256:'")
    hex_part = digest[7:]
    if len(hex_part) != 64:
        raise InvalidDigest(digest, f"hex part must be 64 chars, got {len(hex_part)}")
    if not all(c in "0123456789abcdef" for c in hex_part):
        raise InvalidDigest(digest, "hex part contains invalid characters")


# =============================================================================
# In-Memory Artifact Store
# =============================================================================

class InMemoryArtifactStore:
    """In-memory artifact storage implementing ArtifactStoreProtocol.

    Thread-safe for asyncio (single-threaded event loop).
    All operations are synchronous but wrapped as async for Protocol compliance.

    Attributes:
        _by_key: (kind, artifact_id, digest) -> artifact mapping
        _by_name: (kind, artifact_id) -> list of digests (newest last)
        _by_digest: digest -> (kind, artifact_id) reverse lookup
    """

    def __init__(self) -> None:
        # Primary storage: (kind, name, digest) → frozen artifact
        self._by_key: dict[tuple[ArtifactKind, str, str], Artifact] = {}
        # Version history: (kind, name) → list of digests (newest last)
        self._by_name: dict[tuple[ArtifactKind, str], list[str]] = {}
        # Reverse lookup: digest → (kind, name)
        self._by_digest: dict[str, tuple[ArtifactKind, str]] = {}

    async def offer(
        self,
        artifact: Artifact,
        *,
        created_by: str,
    ) -> Artifact:
        """Publish artifact; compute digest from body.

        Args:
            artifact: The artifact to publish
            created_by: Principal ID of the creator

        Returns:
            Artifact with computed digest and metadata

        Raises:
            ImmutableArtifact: If attempting to mutate existing artifact
        """
        kind = kind_of(artifact)
        body = artifact.body_for_digest()
        digest = content_digest(body)
        key = (kind, artifact.name, digest)

        # Check for existing artifact
        existing = self._by_key.get(key)
        if existing is not None:
            if existing.body_for_digest() != body:
                raise ImmutableArtifact(
                    kind=kind.value,
                    artifact_id=artifact.name,
                    digest=digest,
                )
            return existing

        # Create metadata
        now = datetime.now(timezone.utc)
        metadata = ArtifactMetadata(
            created_by=created_by,
            created_at=now,
            updated_at=now,
            owner_id=created_by,  # Creator is initial owner
        )

        # Store with digest and metadata
        stored = with_digest(artifact, digest)
        stored = with_metadata(stored, metadata)

        self._by_key[key] = stored
        self._by_name.setdefault((kind, artifact.name), []).append(digest)
        self._by_digest[digest] = (kind, artifact.name)

        return stored

    async def get(
        self,
        kind: ArtifactKind,
        artifact_id: str,
        digest: str,
        *,
        require_offered: bool = False,
    ) -> Artifact:
        """Retrieve artifact by kind, id, and digest.

        Args:
            kind: Type of artifact
            artifact_id: Artifact name
            digest: Content hash
            require_offered: If True, raise if status != offered

        Returns:
            The artifact

        Raises:
            DigestMissing: If artifact not found
            DigestMismatch: If stored digest differs
            NotOffered: If require_offered=True and status != offered
        """
        reject_floating_ref(digest)
        reject_floating_ref(artifact_id)

        key = (kind, artifact_id, digest)
        art = self._by_key.get(key)
        if art is None:
            raise DigestMissing(
                kind=kind.value,
                artifact_id=artifact_id,
                digest=digest,
            )

        # Verify integrity
        actual = content_digest(art.body_for_digest())
        if actual != digest:
            raise DigestMismatch(expected=digest, actual=actual)

        if require_offered and art.status != ArtifactStatus.OFFERED:
            raise NotOffered(
                kind=kind.value,
                artifact_id=artifact_id,
                digest=digest,
                status=art.status.value if isinstance(art.status, ArtifactStatus) else str(art.status),
            )

        return art

    async def deprecate(
        self,
        kind: ArtifactKind,
        artifact_id: str,
        digest: str,
        *,
        deprecated_by: str,
    ) -> None:
        """Mark artifact as deprecated.

        Args:
            kind: Type of artifact
            artifact_id: Artifact name
            digest: Content hash
            deprecated_by: Principal ID who deprecated

        Raises:
            DigestMissing: If artifact not found
        """
        art = await self.get(kind, artifact_id, digest)
        key = (kind, artifact_id, digest)

        # Update status and metadata
        deprecated = with_status(art, ArtifactStatus.DEPRECATED)
        if art.metadata:
            now = datetime.now(timezone.utc)
            new_metadata = ArtifactMetadata(
                created_by=art.metadata.created_by,
                created_at=art.metadata.created_at,
                updated_at=now,
                owner_id=art.metadata.owner_id,
            )
            deprecated = with_metadata(deprecated, new_metadata)

        self._by_key[key] = deprecated

    async def list_artifacts(
        self,
        kind: ArtifactKind,
        *,
        status: ArtifactStatus | None = None,
        created_by: str | None = None,
        owner_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Artifact]:
        """List artifacts with optional filters.

        Args:
            kind: Type of artifact to list
            status: Filter by status
            created_by: Filter by creator
            owner_id: Filter by owner
            limit: Max results
            offset: Skip first N results

        Returns:
            List of matching artifacts
        """
        results: list[Artifact] = []

        for (k, _name, _digest), art in self._by_key.items():
            if k != kind:
                continue
            if status is not None and art.status != status:
                continue
            if created_by is not None:
                if art.metadata is None or art.metadata.created_by != created_by:
                    continue
            if owner_id is not None:
                if art.metadata is None or art.metadata.owner_id != owner_id:
                    continue
            results.append(art)

        # Sort by name, then digest for consistency
        results.sort(key=lambda a: (a.name, a.digest))

        # Apply pagination
        return results[offset : offset + limit]

    async def get_by_digest(
        self,
        digest: str,
    ) -> tuple[ArtifactKind, str] | None:
        """Reverse lookup: digest -> (kind, artifact_id).

        Args:
            digest: Content hash to look up

        Returns:
            Tuple of (kind, artifact_id) or None if not found
        """
        return self._by_digest.get(digest)

    async def list_versions(
        self,
        kind: ArtifactKind,
        artifact_id: str,
    ) -> list[str]:
        """List all digests for an artifact (oldest first).

        Args:
            kind: Type of artifact
            artifact_id: Artifact name

        Returns:
            List of digest strings, oldest first
        """
        return list(self._by_name.get((kind, artifact_id), []))

    async def get_metadata(
        self,
        kind: ArtifactKind,
        artifact_id: str,
        digest: str,
    ) -> ArtifactMetadata | None:
        """Get artifact metadata without full body.

        Args:
            kind: Type of artifact
            artifact_id: Artifact name
            digest: Content hash

        Returns:
            Metadata or None if not found
        """
        key = (kind, artifact_id, digest)
        art = self._by_key.get(key)
        if art is None:
            return None
        return art.metadata

    # =========================================================================
    # Additional Methods (not in Protocol)
    # =========================================================================

    def clear(self) -> None:
        """Clear all artifacts (for testing)."""
        self._by_key.clear()
        self._by_name.clear()
        self._by_digest.clear()

    def count(self) -> int:
        """Return total artifact count."""
        return len(self._by_key)


# =============================================================================
# In-Memory Usage Store
# =============================================================================

class InMemoryUsageStore:
    """In-memory usage tracking implementing UsageStoreProtocol.

    Thread-safe for asyncio (single-threaded event loop).

    Attributes:
        _usage_records: List of all usage records
        _error_records: List of error records (kind, artifact_id, digest, error_code, ts)
        _by_artifact: Index by (kind, artifact_id, digest)
        _by_session: Index by session_id
    """

    def __init__(self) -> None:
        self._usage_records: list[ArtifactUsageRecord] = []
        self._error_records: list[tuple[ArtifactKind, str, str, str, int, str | None, str | None]] = []
        # Indexes for fast lookup
        self._by_artifact: dict[tuple[ArtifactKind, str, str], list[int]] = defaultdict(list)
        self._by_session: dict[str, list[int]] = defaultdict(list)
        self._by_principal: dict[str, list[int]] = defaultdict(list)

    async def record_usage(
        self,
        record: ArtifactUsageRecord,
    ) -> None:
        """Record a usage event.

        Args:
            record: Usage event to record
        """
        idx = len(self._usage_records)
        self._usage_records.append(record)

        # Update indexes
        key = (record.artifact_kind, record.artifact_id, record.artifact_digest)
        self._by_artifact[key].append(idx)
        self._by_session[record.session_id].append(idx)
        self._by_principal[record.principal_id].append(idx)

    async def record_error(
        self,
        kind: ArtifactKind,
        artifact_id: str,
        digest: str,
        error_code: str,
        *,
        principal_id: str | None = None,
        session_id: str | None = None,
    ) -> None:
        """Record an error for analytics.

        Args:
            kind: Type of artifact
            artifact_id: Artifact name
            digest: Content hash
            error_code: Error classification
            principal_id: Who triggered the error
            session_id: Session context
        """
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        self._error_records.append(
            (kind, artifact_id, digest, error_code, now_ms, principal_id, session_id)
        )

    async def get_stats(
        self,
        kind: ArtifactKind,
        artifact_id: str,
        digest: str | None = None,
        *,
        since: datetime | None = None,
    ) -> ArtifactUsageStats:
        """Get aggregated usage statistics.

        Args:
            kind: Type of artifact
            artifact_id: Artifact name
            digest: Specific version (None for all)
            since: Only count usage since this time

        Returns:
            Aggregated statistics
        """
        since_ms = int(since.timestamp() * 1000) if since else 0

        resolution_count = 0
        bind_count = 0
        session_count = 0
        principals: set[str] = set()
        last_used_at: datetime | None = None

        for record in self._usage_records:
            if record.artifact_kind != kind:
                continue
            if record.artifact_id != artifact_id:
                continue
            if digest is not None and record.artifact_digest != digest:
                continue
            if record.timestamp_ms < since_ms:
                continue

            # Count by operation
            if record.operation == "resolved":
                resolution_count += 1
            elif record.operation == "bound":
                bind_count += 1
            elif record.operation == "session_started":
                session_count += 1

            principals.add(record.principal_id)

            # Track latest usage
            record_time = datetime.fromtimestamp(
                record.timestamp_ms / 1000, tz=timezone.utc
            )
            if last_used_at is None or record_time > last_used_at:
                last_used_at = record_time

        # Count errors
        error_count = 0
        for err in self._error_records:
            err_kind, err_id, err_digest, _, err_ts, _, _ = err
            if err_kind != kind or err_id != artifact_id:
                continue
            if digest is not None and err_digest != digest:
                continue
            if err_ts < since_ms:
                continue
            error_count += 1

        total_ops = resolution_count + bind_count + session_count
        error_rate = error_count / total_ops if total_ops > 0 else 0.0

        return ArtifactUsageStats(
            artifact_kind=kind,
            artifact_id=artifact_id,
            artifact_digest=digest,
            resolution_count=resolution_count,
            bind_count=bind_count,
            session_count=session_count,
            unique_principals=len(principals),
            last_used_at=last_used_at,
            error_count=error_count,
            error_rate=error_rate,
        )

    async def get_sessions_using(
        self,
        kind: ArtifactKind,
        artifact_id: str,
        digest: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[str]:
        """Get session IDs that used this artifact.

        Args:
            kind: Type of artifact
            artifact_id: Artifact name
            digest: Content hash
            limit: Max results
            offset: Skip first N

        Returns:
            List of session IDs
        """
        key = (kind, artifact_id, digest)
        indices = self._by_artifact.get(key, [])

        # Get unique session IDs in order of first usage
        seen: set[str] = set()
        sessions: list[str] = []
        for idx in indices:
            record = self._usage_records[idx]
            if record.session_id not in seen:
                seen.add(record.session_id)
                sessions.append(record.session_id)

        return sessions[offset : offset + limit]

    async def get_popular_artifacts(
        self,
        kind: ArtifactKind,
        *,
        since_ms: int | None = None,
        limit: int = 10,
    ) -> list[ArtifactUsageStats]:
        """Get most-used artifacts.

        Args:
            kind: Type of artifact
            since_ms: Only count usage since this timestamp
            limit: Max results

        Returns:
            List of usage stats, sorted by usage count desc
        """
        # Aggregate by (artifact_id, digest)
        usage_counts: dict[tuple[str, str], int] = defaultdict(int)

        for record in self._usage_records:
            if record.artifact_kind != kind:
                continue
            if since_ms is not None and record.timestamp_ms < since_ms:
                continue
            key = (record.artifact_id, record.artifact_digest)
            usage_counts[key] += 1

        # Sort by count descending
        sorted_items = sorted(usage_counts.items(), key=lambda x: -x[1])

        # Get stats for top items
        results: list[ArtifactUsageStats] = []
        since_dt = (
            datetime.fromtimestamp(since_ms / 1000, tz=timezone.utc)
            if since_ms
            else None
        )

        for (artifact_id, digest), _ in sorted_items[:limit]:
            stats = await self.get_stats(kind, artifact_id, digest, since=since_dt)
            results.append(stats)

        return results

    async def get_usage_history(
        self,
        kind: ArtifactKind,
        artifact_id: str,
        digest: str | None = None,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ArtifactUsageRecord]:
        """Get raw usage records.

        Args:
            kind: Type of artifact
            artifact_id: Artifact name
            digest: Specific version (None for all)
            limit: Max results
            offset: Skip first N

        Returns:
            List of usage records, newest first
        """
        results: list[ArtifactUsageRecord] = []

        for record in reversed(self._usage_records):
            if record.artifact_kind != kind:
                continue
            if record.artifact_id != artifact_id:
                continue
            if digest is not None and record.artifact_digest != digest:
                continue
            results.append(record)

        return results[offset : offset + limit]

    # =========================================================================
    # Additional Methods (not in Protocol)
    # =========================================================================

    def clear(self) -> None:
        """Clear all records (for testing)."""
        self._usage_records.clear()
        self._error_records.clear()
        self._by_artifact.clear()
        self._by_session.clear()
        self._by_principal.clear()

    def count(self) -> int:
        """Return total record count."""
        return len(self._usage_records)

    def error_count(self) -> int:
        """Return total error count."""
        return len(self._error_records)
