"""Core catalog types, errors, and protocols.

This module contains the foundation layer:
- types.py: All enums, dataclasses, type aliases
- errors.py: Exception hierarchy with rich context
- protocols.py: Protocol interfaces for dependency injection
- digests.py: Content hash computation
"""

from supervisor.catalog.core.types import (
    # Enums
    ArtifactKind,
    ArtifactStatus,
    SandboxProviderName,
    FileEffects,
    ApprovalDefault,
    RecallBackend,
    CatalogGrant,
    CatalogAction,
    # Metadata
    ArtifactMetadata,
    ArtifactUsageRecord,
    ArtifactUsageStats,
    # Artifact types
    PromptSection,
    PromptPack,
    ToolDefinition,
    ToolSet,
    DigestRef,
    PlugBindings,
    AgentComposition,
    SandboxProfile,
    MemoryProfile,
    SessionPins,
    # Type alias
    Artifact,
)

from supervisor.catalog.core.errors import (
    CatalogError,
    DigestMismatch,
    DigestMissing,
    InvalidDigest,
    NotOffered,
    ImmutableArtifact,
    FloatingRef,
    CompositionIncomplete,
    CompositionDigestRequired,
    HarnessVersionMismatch,
    CatalogPermissionDenied,
    CatalogOwnershipRequired,
    CatalogStorageError,
    CatalogConnectionError,
    InvalidArtifact,
)

from supervisor.catalog.core.protocols import (
    ArtifactStoreProtocol,
    UsageStoreProtocol,
    CatalogServiceProtocol,
    CompositionResolverProtocol,
    RuntimeBinderProtocol,
    CatalogAuthzProtocol,
    CatalogAnalyticsProtocol,
)

from supervisor.catalog.core.digests import content_digest

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
    # Types - Metadata
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
    # Digests
    "content_digest",
]
