"""Catalog subsystem — agent composition and artifact management.

This module provides comprehensive artifact lifecycle management:

Types:
    ArtifactKind, ArtifactStatus, CatalogGrant - Enums
    Artifact, PromptPack, ToolSet, etc. - Dataclasses
    ArtifactMetadata, ArtifactUsageRecord - Observability types

Errors:
    CatalogError and subclasses - Rich exception hierarchy

Protocols:
    ArtifactStoreProtocol, UsageStoreProtocol, etc. - Interfaces

Repositories:
    InMemoryArtifactStore - Development/testing
    PostgresArtifactStore - Production

Services:
    CatalogService - Main orchestrator with observability
    CatalogAuthz - Permission service
    CompositionResolver - Composition to pins

Example:
    from supervisor.catalog import (
        CatalogService,
        CatalogAuthz,
        InMemoryArtifactStore,
        InMemoryUsageStore,
        PromptPack,
        ArtifactKind,
    )

    # Setup
    artifact_store = InMemoryArtifactStore()
    usage_store = InMemoryUsageStore()
    authz = CatalogAuthz(admin_principals=["system"])

    catalog = CatalogService(
        artifact_store=artifact_store,
        usage_store=usage_store,
        authz=authz,
    )

    # Publish
    artifact = await catalog.publish("user_123", prompt_pack)
"""

from __future__ import annotations

# =============================================================================
# Errors
# =============================================================================
from supervisor.catalog.core.errors import (
    CatalogConnectionError,
    # Base
    CatalogError,
    CatalogOwnershipRequired,
    # Permission
    CatalogPermissionDenied,
    # Storage
    CatalogStorageError,
    CompositionDigestRequired,
    # Composition
    CompositionIncomplete,
    # Integrity
    DigestMismatch,
    DigestMissing,
    FloatingRef,
    HarnessVersionMismatch,
    ImmutableArtifact,
    # Validation
    InvalidArtifact,
    InvalidDigest,
    # Status
    NotOffered,
)

# =============================================================================
# Protocols
# =============================================================================
from supervisor.catalog.core.protocols import (
    ArtifactStoreProtocol,
    CatalogAnalyticsProtocol,
    CatalogAuthzProtocol,
    CatalogServiceProtocol,
    CompositionResolverProtocol,
    RuntimeBinderProtocol,
    UsageStoreProtocol,
)

# =============================================================================
# Types
# =============================================================================
from supervisor.catalog.core.types import (
    AgentComposition,
    ApprovalDefault,
    # Type alias
    Artifact,
    # Enums
    ArtifactKind,
    # Metadata
    ArtifactMetadata,
    ArtifactStatus,
    ArtifactUsageRecord,
    ArtifactUsageStats,
    CatalogAction,
    CatalogGrant,
    DigestRef,
    FileEffects,
    MemoryProfile,
    PlugBindings,
    PromptPack,
    # Artifact types
    PromptSection,
    RecallBackend,
    SandboxProfile,
    SandboxProviderName,
    SessionPins,
    ToolDefinition,
    ToolSet,
)

# =============================================================================
# Services
# =============================================================================
from supervisor.catalog.services import (
    CatalogAuthz,
    CatalogService,
    CompositionResolver,
)

# =============================================================================
# Repositories
# =============================================================================
from supervisor.catalog.storage import (
    InMemoryArtifactStore,
    InMemoryUsageStore,
    PostgresArtifactStore,
    PostgresUsageStore,
    reject_floating_ref,
    validate_digest_format,
)

# =============================================================================
# Converters
# =============================================================================
from supervisor.catalog.storage.converters import (
    artifact_from_dict,
    artifact_to_dict,
    composition_from_dict,
    composition_to_dict,
    # Helpers
    kind_of,
    memory_profile_from_dict,
    memory_profile_to_dict,
    metadata_from_dict,
    metadata_to_dict,
    # From dict
    prompt_pack_from_dict,
    # To dict
    prompt_pack_to_dict,
    sandbox_profile_from_dict,
    sandbox_profile_to_dict,
    session_pins_from_dict,
    session_pins_to_dict,
    tool_set_from_dict,
    tool_set_to_dict,
    with_digest,
    with_metadata,
    with_status,
)

__all__ = [
    # Types - Enums
    "ArtifactKind",
    "ArtifactStatus",
    "SandboxProviderName",
    "FileEffects",
    "ApprovalDefault",
    "RecallBackend",
    "CatalogGrant",
    "CatalogAction",
    # Types - Metadata/Analytics
    "ArtifactMetadata",
    "ArtifactUsageRecord",
    "ArtifactUsageStats",
    # Types - Artifacts
    "PromptSection",
    "PromptPack",
    "ToolDefinition",
    "ToolSet",
    "DigestRef",
    "PlugBindings",
    "AgentComposition",
    "SandboxProfile",
    "MemoryProfile",
    "SessionPins",
    "Artifact",
    # Errors
    "CatalogError",
    "DigestMismatch",
    "DigestMissing",
    "InvalidDigest",
    "NotOffered",
    "ImmutableArtifact",
    "FloatingRef",
    "CompositionIncomplete",
    "CompositionDigestRequired",
    "HarnessVersionMismatch",
    "CatalogPermissionDenied",
    "CatalogOwnershipRequired",
    "CatalogStorageError",
    "CatalogConnectionError",
    "InvalidArtifact",
    # Protocols
    "ArtifactStoreProtocol",
    "UsageStoreProtocol",
    "CatalogServiceProtocol",
    "CompositionResolverProtocol",
    "RuntimeBinderProtocol",
    "CatalogAuthzProtocol",
    "CatalogAnalyticsProtocol",
    # Converters
    "prompt_pack_from_dict",
    "tool_set_from_dict",
    "composition_from_dict",
    "sandbox_profile_from_dict",
    "memory_profile_from_dict",
    "session_pins_from_dict",
    "metadata_from_dict",
    "artifact_from_dict",
    "prompt_pack_to_dict",
    "tool_set_to_dict",
    "composition_to_dict",
    "sandbox_profile_to_dict",
    "memory_profile_to_dict",
    "session_pins_to_dict",
    "metadata_to_dict",
    "artifact_to_dict",
    "kind_of",
    "with_digest",
    "with_status",
    "with_metadata",
    # Repositories
    "InMemoryArtifactStore",
    "InMemoryUsageStore",
    "PostgresArtifactStore",
    "PostgresUsageStore",
    "reject_floating_ref",
    "validate_digest_format",
    # Services
    "CatalogService",
    "CatalogAuthz",
    "CompositionResolver",
]
