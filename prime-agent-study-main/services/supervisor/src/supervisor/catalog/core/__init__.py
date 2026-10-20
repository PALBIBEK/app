"""Core catalog types, errors, and protocols.

This module contains the foundation layer:
- types.py: All enums, dataclasses, type aliases
- errors.py: Exception hierarchy with rich context
- protocols.py: Protocol interfaces for dependency injection
- digests.py: Content hash computation
"""

from supervisor.catalog.core.digests import content_digest
from supervisor.catalog.core.errors import (
    CatalogError,
    CatalogOwnershipRequired,
    CompositionDigestRequired,
    CompositionIncomplete,
    DigestMismatch,
    DigestMissing,
    FloatingRef,
    HarnessVersionMismatch,
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
    Artifact,
    ArtifactKind,
    ArtifactMetadata,
    ArtifactStatus,
    ArtifactUsageRecord,
    ArtifactUsageStats,
    CatalogGrant,
    DigestRef,
    FileEffects,
    MemoryProfile,
    PlugBindings,
    PromptPack,
    PromptSection,
    RecallBackend,
    SandboxProfile,
    SandboxProviderName,
    SessionPins,
    ToolDefinition,
    ToolSet,
)

__all__ = [
    "AgentComposition",
    "ApprovalDefault",
    "Artifact",
    "ArtifactKind",
    "ArtifactMetadata",
    "ArtifactStatus",
    "ArtifactStoreProtocol",
    "ArtifactUsageRecord",
    "ArtifactUsageStats",
    "CatalogAnalyticsProtocol",
    "CatalogAuthzProtocol",
    "CatalogError",
    "CatalogGrant",
    "CatalogOwnershipRequired",
    "CatalogServiceProtocol",
    "CompositionDigestRequired",
    "CompositionIncomplete",
    "CompositionResolverProtocol",
    "DigestMismatch",
    "DigestMissing",
    "DigestRef",
    "FileEffects",
    "FloatingRef",
    "HarnessVersionMismatch",
    "InvalidArtifact",
    "InvalidDigest",
    "MemoryProfile",
    "NotOffered",
    "PlugBindings",
    "PromptPack",
    "PromptSection",
    "RecallBackend",
    "RuntimeBinderProtocol",
    "SandboxProfile",
    "SandboxProviderName",
    "SessionPins",
    "ToolDefinition",
    "ToolSet",
    "UsageStoreProtocol",
    "content_digest",
]
