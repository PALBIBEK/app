"""Core catalog types, errors, and protocols.

This module contains the foundation layer:
- types.py: All enums, dataclasses, type aliases
- errors.py: Exception hierarchy with rich context
- protocols.py: Protocol interfaces for dependency injection
- digests.py: Content hash computation
"""

from supervisor.catalog.core.digests import content_digest
from supervisor.catalog.core.errors import (
    CatalogConnectionError,
    CatalogError,
    CatalogOwnershipRequired,
    CatalogPermissionDenied,
    CatalogStorageError,
    CompositionDigestRequired,
    CompositionIncomplete,
    DigestMismatch,
    DigestMissing,
    FloatingRef,
    HarnessVersionMismatch,
    ImmutableArtifact,
    InvalidArtifact,
    InvalidDigest,
    NotOffered,
)
from supervisor.catalog.core.protocols import (
    ArtifactStoreProtocol,
    CatalogAnalyticsProtocol,
    CatalogAuthzProtocol,
    CatalogServiceProtocol,
    CompositionResolverProtocol,
    RuntimeBinderProtocol,
    UsageStoreProtocol,
)
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
