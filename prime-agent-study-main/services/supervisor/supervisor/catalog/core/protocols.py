"""Protocol interfaces for catalog subsystem - dependency injection.

All catalog components defined as Protocol classes to enable:
- Dependency injection in FastAPI
- Easy mocking in tests
- Swappable implementations (memory vs Postgres)
- Clear interface contracts

Reference: policy/protocols.py pattern.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from datetime import datetime

    from supervisor.catalog.core.types import (
        Artifact,
        ArtifactKind,
        ArtifactStatus,
        ArtifactUsageRecord,
        ArtifactUsageStats,
        SessionPins,
    )


__all__ = [
    # Store protocols
    "ArtifactStoreProtocol",
    # Analytics protocol
    # Auth protocol
    # Service protocols
    "CompositionResolverProtocol",
    "UsageStoreProtocol",
]


# =============================================================================
# Artifact Store Protocol
# =============================================================================

@runtime_checkable
class ArtifactStoreProtocol(Protocol):
    """Interface for artifact persistence.

    Implementations:
    - InMemoryArtifactStore: For development/testing
    - PostgresArtifactStore: For production

    All methods are async to support both implementations.
    """

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
            InvalidArtifact: If artifact data is malformed
        """
        ...

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
        ...

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
        ...

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
        ...

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
        ...

    async def list_versions(
        self,
        kind: ArtifactKind,
        artifact_id: str,
    ) -> list[str]:
        """List all digests for an artifact (newest last).

        Args:
            kind: Type of artifact
            artifact_id: Artifact name

        Returns:
            List of digest strings, oldest first
        """
        ...



# =============================================================================
# Usage Store Protocol
# =============================================================================

@runtime_checkable
class UsageStoreProtocol(Protocol):
    """Interface for usage tracking and analytics.

    Records all artifact usage for analytics dashboards.
    """

    async def record_usage(
        self,
        record: ArtifactUsageRecord,
    ) -> None:
        """Record a usage event.

        Args:
            record: Usage event to record
        """
        ...

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
        ...

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
        ...

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
        ...

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
        ...

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
        ...


# =============================================================================
# Catalog Service Protocol
# =============================================================================



# =============================================================================
# Composition Resolver Protocol
# =============================================================================

@runtime_checkable
class CompositionResolverProtocol(Protocol):
    """Composition resolution to session pins.

    Resolves composition reference to verified session pins,
    ensuring all child artifacts exist and are offered.
    """

    async def resolve(
        self,
        *,
        composition_id: str | None = None,
        composition_digest: str | None = None,
        require_digest: bool = False,
    ) -> SessionPins:
        """Resolve composition to session pins.

        Args:
            composition_id: Composition name (for tip resolve)
            composition_digest: Exact digest
            require_digest: Reject id-only resolve

        Returns:
            Session pins with all artifact digests

        Raises:
            CompositionIncomplete: If missing required pins
            CompositionDigestRequired: If require_digest and no digest
            DigestMissing: If composition not found
            NotOffered: If any artifact not offered
        """
        ...



# =============================================================================
# Runtime Binder Protocol
# =============================================================================



# =============================================================================
# Catalog Authorization Protocol
# =============================================================================



# =============================================================================
# Catalog Analytics Protocol
# =============================================================================

