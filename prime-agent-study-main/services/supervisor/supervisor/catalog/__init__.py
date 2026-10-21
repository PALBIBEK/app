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

# =============================================================================
# Protocols
# =============================================================================
from supervisor.catalog.core.protocols import (
    ArtifactStoreProtocol,
    CompositionResolverProtocol,
    UsageStoreProtocol,
)

# =============================================================================
# Types
# =============================================================================
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

# =============================================================================
# Services
# =============================================================================
# =============================================================================
# Repositories
# =============================================================================
from supervisor.catalog.storage import reject_floating_ref, validate_digest_format

# =============================================================================
# Converters
# =============================================================================
from supervisor.catalog.storage.converters import (
    artifact_from_dict,
    artifact_to_dict,
    composition_from_dict,
    composition_to_dict,
    kind_of,
    memory_profile_from_dict,
    memory_profile_to_dict,
    metadata_from_dict,
    metadata_to_dict,
    prompt_pack_from_dict,
    prompt_pack_to_dict,
    sandbox_profile_from_dict,
    sandbox_profile_to_dict,
    tool_set_from_dict,
    tool_set_to_dict,
    with_digest,
    with_status,
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
    "CatalogError",
    "CatalogGrant",
    "CatalogOwnershipRequired",
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
    "SandboxProfile",
    "SandboxProviderName",
    "SessionPins",
    "ToolDefinition",
    "ToolSet",
    "UsageStoreProtocol",
    "artifact_from_dict",
    "artifact_to_dict",
    "composition_from_dict",
    "composition_to_dict",
    "kind_of",
    "memory_profile_from_dict",
    "memory_profile_to_dict",
    "metadata_from_dict",
    "metadata_to_dict",
    "prompt_pack_from_dict",
    "prompt_pack_to_dict",
    "reject_floating_ref",
    "sandbox_profile_from_dict",
    "sandbox_profile_to_dict",
    "tool_set_from_dict",
    "tool_set_to_dict",
    "validate_digest_format",
    "with_digest",
    "with_status",
]
