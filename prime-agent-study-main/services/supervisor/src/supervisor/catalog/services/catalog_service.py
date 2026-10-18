"""Catalog service — high-level orchestration with observability.

Implements CatalogServiceProtocol with:
- Permission checks via CatalogAuthz
- Audit logging for all operations
- Usage tracking for analytics
- Metric emission

Design principles:
- Single entry point for all catalog operations
- Every operation is logged and tracked
- Permission checks before any state mutation
- Composition via Protocol interfaces

Reference: policy/approvals.py ApprovalBroker pattern.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from supervisor.catalog.storage.converters import kind_of
from supervisor.catalog.core.errors import CatalogPermissionDenied
from supervisor.catalog.core.types import (
    ArtifactKind,
    ArtifactStatus,
    ArtifactUsageRecord,
    ArtifactUsageStats,
    CatalogAction,
    CatalogGrant,
    SessionPins,
)

if TYPE_CHECKING:
    from supervisor.catalog.core.protocols import (
        ArtifactStoreProtocol,
        CatalogAuthzProtocol,
        CompositionResolverProtocol,
        UsageStoreProtocol,
    )
    from supervisor.catalog.core.types import Artifact
    from supervisor.policy import AuditLoggerProtocol


__all__ = [
    "CatalogService",
]

logger = logging.getLogger(__name__)


class CatalogService:
    """High-level catalog operations with observability.

    Implements CatalogServiceProtocol.

    Wraps artifact store with:
    - Permission checks
    - Audit logging
    - Usage tracking
    - Metrics emission

    Attributes:
        _artifact_store: Storage backend for artifacts
        _usage_store: Storage backend for usage analytics
        _authz: Authorization service
        _audit_logger: Audit event logger
        _resolver: Composition resolver
    """

    def __init__(
        self,
        artifact_store: ArtifactStoreProtocol,
        usage_store: UsageStoreProtocol,
        authz: CatalogAuthzProtocol,
        *,
        audit_logger: AuditLoggerProtocol | None = None,
        resolver: CompositionResolverProtocol | None = None,
    ) -> None:
        """Initialize the catalog service.

        Args:
            artifact_store: Storage backend for artifacts
            usage_store: Storage backend for usage analytics
            authz: Authorization service
            audit_logger: Audit event logger (optional)
            resolver: Composition resolver (optional, created if not provided)
        """
        self._artifact_store = artifact_store
        self._usage_store = usage_store
        self._authz = authz
        self._audit_logger = audit_logger

        # Create resolver if not provided
        if resolver is None:
            from supervisor.catalog.services.resolver import CompositionResolver
            self._resolver = CompositionResolver(artifact_store)
        else:
            self._resolver = resolver

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
        kind = kind_of(artifact)

        # Check permission
        await self._authz.require(
            principal_id,
            CatalogGrant.ARTIFACT_PUBLISH,
            artifact_kind=kind,
        )

        # Perform publish
        published = await self._artifact_store.offer(
            artifact,
            created_by=principal_id,
        )

        # Log audit event
        await self._log_audit(
            principal_id=principal_id,
            action=CatalogAction.ARTIFACT_PUBLISHED,
            artifact_kind=kind,
            artifact_id=published.name,
            artifact_digest=published.digest,
        )

        logger.info(
            "Published artifact: kind=%s id=%s digest=%s by=%s",
            kind.value,
            published.name,
            published.digest[:16] + "...",
            principal_id,
        )

        return published

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
        # Check ownership or admin
        await self._authz.require_owner_or_admin(
            principal_id,
            kind,
            artifact_id,
            digest,
        )

        # Perform deprecate
        await self._artifact_store.deprecate(
            kind,
            artifact_id,
            digest,
            deprecated_by=principal_id,
        )

        # Log audit event
        await self._log_audit(
            principal_id=principal_id,
            action=CatalogAction.ARTIFACT_DEPRECATED,
            artifact_kind=kind,
            artifact_id=artifact_id,
            artifact_digest=digest,
        )

        logger.info(
            "Deprecated artifact: kind=%s id=%s digest=%s by=%s",
            kind.value,
            artifact_id,
            digest[:16] + "...",
            principal_id,
        )

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
        # Check read permission
        await self._authz.require(
            principal_id,
            CatalogGrant.ARTIFACT_READ,
            artifact_kind=kind,
            artifact_id=artifact_id,
        )

        # Get artifact
        artifact = await self._artifact_store.get(
            kind,
            artifact_id,
            digest,
            require_offered=require_offered,
        )

        return artifact

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
        # Check read permission
        await self._authz.require(
            principal_id,
            CatalogGrant.ARTIFACT_READ,
            artifact_kind=kind,
        )

        return await self._artifact_store.list_artifacts(
            kind,
            status=status,
            limit=limit,
            offset=offset,
        )

    async def resolve_composition(
        self,
        principal_id: str,
        *,
        composition_id: str | None = None,
        composition_digest: str | None = None,
        require_digest: bool = False,
        session_id: str | None = None,
    ) -> SessionPins:
        """Resolve composition to session pins.

        Args:
            principal_id: Who is resolving
            composition_id: Composition name
            composition_digest: Exact digest
            require_digest: Reject id-only resolve
            session_id: Session ID for tracking (optional)

        Returns:
            Session pins

        Raises:
            CatalogPermissionDenied: If lacking composition use grant
        """
        # Check permission
        await self._authz.require(
            principal_id,
            CatalogGrant.COMPOSITION_USE,
        )

        # Resolve
        pins = await self._resolver.resolve(
            composition_id=composition_id,
            composition_digest=composition_digest,
            require_digest=require_digest,
        )

        # Record usage
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        await self._usage_store.record_usage(
            ArtifactUsageRecord(
                artifact_kind=ArtifactKind.COMPOSITION,
                artifact_id=pins.composition_id,
                artifact_digest=pins.composition_digest,
                session_id=session_id or "unknown",
                principal_id=principal_id,
                timestamp_ms=now_ms,
                operation="resolved",
            )
        )

        # Log audit event
        await self._log_audit(
            principal_id=principal_id,
            action=CatalogAction.ARTIFACT_RESOLVED,
            artifact_kind=ArtifactKind.COMPOSITION,
            artifact_id=pins.composition_id,
            artifact_digest=pins.composition_digest,
            session_id=session_id,
        )

        return pins

    async def record_session_start(
        self,
        principal_id: str,
        session_id: str,
        pins: SessionPins,
    ) -> None:
        """Record session start for analytics.

        Args:
            principal_id: Who started the session
            session_id: Session ID
            pins: Session pins
        """
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

        # Record composition usage
        await self._usage_store.record_usage(
            ArtifactUsageRecord(
                artifact_kind=ArtifactKind.COMPOSITION,
                artifact_id=pins.composition_id,
                artifact_digest=pins.composition_digest,
                session_id=session_id,
                principal_id=principal_id,
                timestamp_ms=now_ms,
                operation="session_started",
            )
        )

        # Record prompt pack usage
        await self._usage_store.record_usage(
            ArtifactUsageRecord(
                artifact_kind=ArtifactKind.PROMPT_PACK,
                artifact_id=pins.prompt_pack_id,
                artifact_digest=pins.prompt_pack_digest,
                session_id=session_id,
                principal_id=principal_id,
                timestamp_ms=now_ms,
                operation="session_started",
            )
        )

        # Record tool set usage
        await self._usage_store.record_usage(
            ArtifactUsageRecord(
                artifact_kind=ArtifactKind.TOOL_SET,
                artifact_id=pins.tool_set_id,
                artifact_digest=pins.tool_set_digest,
                session_id=session_id,
                principal_id=principal_id,
                timestamp_ms=now_ms,
                operation="session_started",
            )
        )

        # Log audit event
        await self._log_audit(
            principal_id=principal_id,
            action=CatalogAction.SESSION_STARTED,
            artifact_kind=ArtifactKind.COMPOSITION,
            artifact_id=pins.composition_id,
            artifact_digest=pins.composition_digest,
            session_id=session_id,
        )

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
        # Check permission
        await self._authz.require(
            principal_id,
            CatalogGrant.ANALYTICS_VIEW,
        )

        return await self._usage_store.get_stats(kind, artifact_id, digest)

    async def get_sessions_using(
        self,
        principal_id: str,
        kind: ArtifactKind,
        artifact_id: str,
        digest: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[str]:
        """Get session IDs using an artifact.

        Args:
            principal_id: Who is requesting
            kind: Type of artifact
            artifact_id: Artifact name
            digest: Content hash
            limit: Max results
            offset: Skip first N

        Returns:
            List of session IDs

        Raises:
            CatalogPermissionDenied: If lacking analytics grant
        """
        await self._authz.require(
            principal_id,
            CatalogGrant.ANALYTICS_VIEW,
        )

        return await self._usage_store.get_sessions_using(
            kind, artifact_id, digest, limit=limit, offset=offset
        )

    async def get_popular_artifacts(
        self,
        principal_id: str,
        kind: ArtifactKind,
        *,
        since_ms: int | None = None,
        limit: int = 10,
    ) -> list[ArtifactUsageStats]:
        """Get most-used artifacts.

        Args:
            principal_id: Who is requesting
            kind: Type of artifact
            since_ms: Only count since timestamp
            limit: Max results

        Returns:
            List of usage stats

        Raises:
            CatalogPermissionDenied: If lacking analytics grant
        """
        await self._authz.require(
            principal_id,
            CatalogGrant.ANALYTICS_VIEW,
        )

        return await self._usage_store.get_popular_artifacts(
            kind, since_ms=since_ms, limit=limit
        )

    async def _log_audit(
        self,
        *,
        principal_id: str,
        action: CatalogAction,
        artifact_kind: ArtifactKind,
        artifact_id: str,
        artifact_digest: str,
        session_id: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Log an audit event.

        Args:
            principal_id: Who performed the action
            action: What action was performed
            artifact_kind: Type of artifact
            artifact_id: Artifact name
            artifact_digest: Content hash
            session_id: Session context (optional)
            extra: Additional context (optional)
        """
        if self._audit_logger is None:
            return

        try:
            from supervisor.policy import AuditEvent, AuditAction

            event = AuditEvent(
                action=AuditAction.CATALOG_ACTION,
                principal_id=principal_id,
                session_id=session_id,
                metadata={
                    "catalog_action": action.value,
                    "artifact_kind": artifact_kind.value,
                    "artifact_id": artifact_id,
                    "artifact_digest": artifact_digest,
                    **(extra or {}),
                },
            )
            await self._audit_logger.log(event)
        except Exception as e:
            logger.warning("Failed to log audit event: %s", e)
