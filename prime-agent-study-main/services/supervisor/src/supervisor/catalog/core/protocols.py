"""Protocol interfaces for catalog subsystem - dependency injection.

All catalog components defined as Protocol classes to enable:
- Dependency injection in FastAPI
- Easy mocking in tests
- Swappable implementations (memory vs Postgres)
- Clear interface contracts

Reference: policy/protocols.py pattern.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from datetime import datetime

    from supervisor.catalog.core.types import (
        Artifact,
        ArtifactKind,
        ArtifactMetadata,
        ArtifactStatus,
        ArtifactUsageRecord,
        ArtifactUsageStats,
        CatalogGrant,
        SessionPins,
    )


__all__ = [
    # Store protocols
    "ArtifactStoreProtocol",
    "UsageStoreProtocol",
    # Service protocols
    "CatalogServiceProtocol",
    "CompositionResolverProtocol",
    "RuntimeBinderProtocol",
    # Auth protocol
    "CatalogAuthzProtocol",
    # Analytics protocol
    "CatalogAnalyticsProtocol",
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

@runtime_checkable
class CatalogServiceProtocol(Protocol):
    """High-level catalog operations with observability.

    Wraps artifact store with:
    - Permission checks
    - Audit logging
    - Usage tracking
    - Metrics emission
    """

    async def publish(
        self,
        principal_id: str,
        artifact: Artifact,
    ) -> Artifact:
        """Publish artifact with permission check and audit logging.

        Args:
            principal_id: Who is publishing
            artifact: Artifact to publish

        Returns:
            Published artifact with digest

        Raises:
            CatalogPermissionDenied: If principal lacks publish grant
        """
        ...

    async def deprecate(
        self,
        principal_id: str,
        kind: ArtifactKind,
        artifact_id: str,
        digest: str,
    ) -> None:
        """Deprecate artifact with permission check.

        Args:
            principal_id: Who is deprecating
            kind: Type of artifact
            artifact_id: Artifact name
            digest: Content hash

        Raises:
            CatalogPermissionDenied: If principal lacks deprecate grant
            CatalogOwnershipRequired: If not owner and not admin
        """
        ...

    async def get(
        self,
        principal_id: str,
        kind: ArtifactKind,
        artifact_id: str,
        digest: str,
        *,
        require_offered: bool = False,
    ) -> Artifact:
        """Get artifact with access logging.

        Args:
            principal_id: Who is accessing
            kind: Type of artifact
            artifact_id: Artifact name
            digest: Content hash
            require_offered: Require offered status

        Returns:
            The artifact
        """
        ...

    async def list_artifacts(
        self,
        principal_id: str,
        kind: ArtifactKind,
        *,
        status: ArtifactStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Artifact]:
        """List artifacts visible to principal.

        Args:
            principal_id: Who is listing
            kind: Type of artifact
            status: Filter by status
            limit: Max results
            offset: Skip first N

        Returns:
            List of artifacts
        """
        ...

    async def get_usage_stats(
        self,
        principal_id: str,
        kind: ArtifactKind,
        artifact_id: str,
        digest: str | None = None,
    ) -> ArtifactUsageStats:
        """Get usage statistics for an artifact.

        Args:
            principal_id: Who is requesting
            kind: Type of artifact
            artifact_id: Artifact name
            digest: Specific version (None for all)

        Returns:
            Usage statistics

        Raises:
            CatalogPermissionDenied: If lacking analytics grant
        """
        ...


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

    async def validate_pins(
        self,
        pins: SessionPins,
    ) -> list[str]:
        """Validate that pinned artifacts still exist and are offered.

        Args:
            pins: Session pins to validate

        Returns:
            List of validation problems (empty if valid)
        """
        ...


# =============================================================================
# Runtime Binder Protocol
# =============================================================================

@runtime_checkable
class RuntimeBinderProtocol(Protocol):
    """Binds session pins to runtime seams.

    Converts abstract pins to concrete runtime objects
    (prompt assembler, tool provider, etc).
    """

    async def bind(
        self,
        pins: SessionPins,
    ) -> Any:  # Returns RuntimeSeams
        """Bind pins to runtime seams.

        Args:
            pins: Session pins with artifact digests

        Returns:
            RuntimeSeams with bound providers
        """
        ...


# =============================================================================
# Catalog Authorization Protocol
# =============================================================================

@runtime_checkable
class CatalogAuthzProtocol(Protocol):
    """Authorization for catalog operations.

    Checks permissions for catalog operations, supporting
    both grant-based and ownership-based access control.
    """

    async def check(
        self,
        principal_id: str,
        grant: CatalogGrant,
        *,
        artifact_kind: ArtifactKind | None = None,
        artifact_id: str | None = None,
        artifact_owner: str | None = None,
    ) -> bool:
        """Check if principal has grant.

        Args:
            principal_id: Who to check
            grant: Required grant
            artifact_kind: Context (optional)
            artifact_id: Context (optional)
            artifact_owner: For ownership checks

        Returns:
            True if granted
        """
        ...

    async def require(
        self,
        principal_id: str,
        grant: CatalogGrant,
        *,
        artifact_kind: ArtifactKind | None = None,
        artifact_id: str | None = None,
        artifact_owner: str | None = None,
    ) -> None:
        """Require grant, raise if missing.

        Args:
            principal_id: Who to check
            grant: Required grant
            artifact_kind: Context (optional)
            artifact_id: Context (optional)
            artifact_owner: For ownership checks

        Raises:
            CatalogPermissionDenied: If grant not held
            CatalogOwnershipRequired: If ownership required but not met
        """
        ...

    async def is_owner(
        self,
        principal_id: str,
        artifact_kind: ArtifactKind,
        artifact_id: str,
        digest: str,
    ) -> bool:
        """Check if principal owns artifact.

        Args:
            principal_id: Who to check
            artifact_kind: Type of artifact
            artifact_id: Artifact name
            digest: Content hash

        Returns:
            True if owner
        """
        ...

    async def is_admin(
        self,
        principal_id: str,
    ) -> bool:
        """Check if principal has admin grant.

        Args:
            principal_id: Who to check

        Returns:
            True if admin
        """
        ...


# =============================================================================
# Catalog Analytics Protocol
# =============================================================================

@runtime_checkable
class CatalogAnalyticsProtocol(Protocol):
    """Catalog-level analytics and metrics.

    Provides aggregated views across all artifacts.
    """

    async def get_artifact_counts(
        self,
    ) -> dict[ArtifactKind, dict[ArtifactStatus, int]]:
        """Get counts of artifacts by kind and status.

        Returns:
            Nested dict of kind -> status -> count
        """
        ...

    async def get_top_compositions(
        self,
        *,
        limit: int = 10,
        since_ms: int | None = None,
    ) -> list[ArtifactUsageStats]:
        """Get most-used compositions.

        Args:
            limit: Max results
            since_ms: Only count usage since timestamp

        Returns:
            List of composition usage stats
        """
        ...

    async def get_error_summary(
        self,
        *,
        since_ms: int | None = None,
    ) -> dict[str, int]:
        """Get error counts by error code.

        Args:
            since_ms: Only count since timestamp

        Returns:
            Dict of error_code -> count
        """
        ...

    async def get_active_principals(
        self,
        *,
        since_ms: int | None = None,
        limit: int = 100,
    ) -> list[tuple[str, int]]:
        """Get principals with most catalog activity.

        Args:
            since_ms: Only count since timestamp
            limit: Max results

        Returns:
            List of (principal_id, usage_count)
        """
        ...
