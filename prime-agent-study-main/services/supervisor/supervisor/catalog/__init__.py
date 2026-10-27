"""The composition catalog: versioned, digest-addressed artifacts that agents are built from.

An agent is a ``composition`` pinning, by digest, a ``prompt_pack``, a ``tool_set``,
and optionally a ``sandbox_profile`` (plus named hands) and a ``memory_profile``.
A session resolves a composition once, at creation, into ``SessionPins`` and runs
those exact versions for its whole life.

Layout:
    core/        types, errors, digests (the vocabulary; no I/O)
    storage/     ``CompositionCatalog`` (the per-process cache) and the dict converters
    repository   the ``catalog_artifacts`` table: the source of truth the cache syncs from
    loading/     the shipped artifacts (``harness/``) loaded and verified at boot
    services/    resolving a composition to pins; what publishing refuses
    providers/   pins bound to runtime seams (prompt, tools, memory, model route)
    usage        how many sessions pin an artifact

Ownership is per tenant: a name belongs to the first tenant that publishes it;
shipped artifacts belong to ``SYSTEM_OWNER`` and every tenant may use them.
"""

from __future__ import annotations

from supervisor.catalog.core.digests import (
    content_digest,
    reject_floating_ref,
    validate_digest_format,
    verify_digest,
)
from supervisor.catalog.core.errors import (
    CatalogError,
    CatalogOwnershipRequired,
    CompositionDigestRequired,
    CompositionIncompatible,
    CompositionIncomplete,
    DigestMismatch,
    DigestMissing,
    FloatingRef,
    HarnessVersionMismatch,
    InvalidArtifact,
    InvalidDigest,
    NotOffered,
)
from supervisor.catalog.core.types import (
    DEFAULT_PROTOCOL_VERSION,
    SUPPORTED_PROTOCOL_VERSIONS,
    AgentComposition,
    ApprovalDefault,
    Artifact,
    ArtifactKind,
    ArtifactMetadata,
    ArtifactStatus,
    ArtifactUsageStats,
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
from supervisor.catalog.storage import (
    SYSTEM_OWNER,
    CompositionCatalog,
    artifact_from_dict,
    artifact_to_dict,
    composition_from_dict,
    kind_of,
    memory_profile_from_dict,
    prompt_pack_from_dict,
    sandbox_profile_from_dict,
    tool_set_from_dict,
    with_digest,
    with_metadata,
    with_status,
)

__all__ = [
    "DEFAULT_PROTOCOL_VERSION",
    "SUPPORTED_PROTOCOL_VERSIONS",
    "SYSTEM_OWNER",
    "AgentComposition",
    "ApprovalDefault",
    "Artifact",
    "ArtifactKind",
    "ArtifactMetadata",
    "ArtifactStatus",
    "ArtifactUsageStats",
    "CatalogError",
    "CatalogOwnershipRequired",
    "CompositionCatalog",
    "CompositionDigestRequired",
    "CompositionIncompatible",
    "CompositionIncomplete",
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
    "artifact_from_dict",
    "artifact_to_dict",
    "composition_from_dict",
    "content_digest",
    "kind_of",
    "memory_profile_from_dict",
    "prompt_pack_from_dict",
    "reject_floating_ref",
    "sandbox_profile_from_dict",
    "tool_set_from_dict",
    "validate_digest_format",
    "verify_digest",
    "with_digest",
    "with_metadata",
    "with_status",
]
