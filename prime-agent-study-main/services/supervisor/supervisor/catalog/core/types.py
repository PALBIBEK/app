"""What the catalog holds: the artifact kinds, their lifecycle, and the pins a session keeps.

Artifacts are frozen: a published version never changes, and is addressed by the
digest of ``body_for_digest()`` (``core.digests``). Fields outside that body
(``digest``, ``status``, ``metadata``) describe a version without being part of it.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Final

__all__ = [
    "DEFAULT_PROTOCOL_VERSION",
    "SUPPORTED_PROTOCOL_VERSIONS",
    "AgentComposition",
    "ApprovalDefault",
    "Artifact",
    "ArtifactKind",
    "ArtifactMetadata",
    "ArtifactStatus",
    "ArtifactUsageStats",
    "DigestRef",
    "FileEffects",
    "MemoryProfile",
    "PlugBindings",
    "PromptPack",
    "PromptSection",
    "RecallBackend",
    "SandboxProfile",
    "SandboxProviderName",
    "SessionPins",
    "ToolDefinition",
    "ToolSet",
]


# =============================================================================
# Constants
# =============================================================================

DEFAULT_PROTOCOL_VERSION: Final[int] = 1
"""The artifact schema an artifact that names none is read as."""

SUPPORTED_PROTOCOL_VERSIONS: Final[frozenset[int]] = frozenset({DEFAULT_PROTOCOL_VERSION})
"""Artifact schemas this supervisor can run; publishing another is refused."""


# =============================================================================
# Enums
# =============================================================================

class ArtifactKind(StrEnum):
    """Type of catalog artifact."""

    PROMPT_PACK = "prompt_pack"
    TOOL_SET = "tool_set"
    COMPOSITION = "composition"
    SANDBOX_PROFILE = "sandbox_profile"
    MEMORY_PROFILE = "memory_profile"


class ArtifactStatus(StrEnum):
    """Lifecycle of an artifact version.

    ``draft``: written but not offered (a shipped file marked draft is not loaded);
    ``offered``: new sessions may pin it; ``deprecated``: sessions already pinned to
    it keep running, new ones may not pin it.
    """

    DRAFT = "draft"
    OFFERED = "offered"
    DEPRECATED = "deprecated"


class SandboxProviderName(StrEnum):
    """Sandbox execution environment provider."""

    NONE = "none"
    LOCAL_PROCESS = "local_process"
    LOCAL_DOCKER = "local_docker"
    E2B = "e2b"
    RUNNER = "runner"  # a tenant's own machine, through its runner (``hands.runners``)


class FileEffects(StrEnum):
    """File system access level for sandbox."""

    READ_ONLY = "read_only"
    WORKSPACE_WRITE = "workspace_write"
    DANGER_FULL_ACCESS = "danger_full_access"


class ApprovalDefault(StrEnum):
    """Default approval behavior for sandbox operations."""

    ASK = "ask"
    NEVER = "never"


class RecallBackend(StrEnum):
    """Memory recall backend type."""

    NONE = "none"
    GITHI_MARKDOWN = "githi_markdown"
    MCP = "mcp"
    # Both route to DurableRecallProvider (seams/core/factory.py).
    DURABLE = "durable"
    MEMORY_SERVICE = "memory_service"


# =============================================================================
# Base Types
# =============================================================================

@dataclass(frozen=True, slots=True)
class ArtifactMetadata:
    """Who published a version and when, and who deprecated it (from its catalog row).

    Not part of the digest: the same content published twice is the same artifact.
    """

    created_by: str
    created_at: datetime
    updated_at: datetime
    owner_id: str | None = None
    deprecated_by: str | None = None
    deprecated_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "owner_id": self.owner_id,
            "deprecated_by": self.deprecated_by,
            "deprecated_at": self.deprecated_at.isoformat() if self.deprecated_at else None,
        }


@dataclass(frozen=True, slots=True)
class DigestRef:
    """Reference to an artifact by id and content digest.

    Used for composition references to child artifacts.
    """

    id: str
    digest: str

    def to_dict(self) -> dict[str, str]:
        """Convert to dictionary."""
        return {"id": self.id, "digest": self.digest}

    @classmethod
    def from_dict(cls, data: dict[str, str]) -> DigestRef:
        """Create from dictionary."""
        return cls(id=data["id"], digest=data["digest"])


# =============================================================================
# Prompt Pack
# =============================================================================

@dataclass(frozen=True, slots=True)
class PromptSection:
    """A section within a prompt pack.

    Sections are ordered and concatenated to form the full system prompt.

    Attributes:
        id: Unique section identifier
        order: Sort order (lower = earlier)
        text: Section text content (may contain template vars)
        complete: False marks a section still being written; publish refuses a
            pack with one, since the model would receive it as written
    """

    id: str
    order: int
    text: str
    complete: bool = True


@dataclass(frozen=True, slots=True)
class PromptPack:
    """Versioned prompt template for agent persona.

    Contains ordered sections that form the system prompt.

    Attributes:
        name: Unique artifact name
        semver: Semantic version string
        digest: Content hash (sha256:...)
        protocol_version: Schema version for compatibility
        status: Lifecycle status (draft/offered/deprecated)
        persona: Optional persona description
        sections: Ordered prompt sections
        vars: Template variable names
        metadata: Creator and ownership info
    """

    name: str
    semver: str
    digest: str
    protocol_version: int
    status: ArtifactStatus
    persona: str | None
    sections: tuple[PromptSection, ...]
    vars: tuple[str, ...] = ()
    metadata: ArtifactMetadata | None = None

    def body_for_digest(self) -> dict[str, Any]:
        """Canonical content body (excludes digest, status, metadata).

        Used for computing content hash. Must be deterministic.
        """
        return {
            "name": self.name,
            "semver": self.semver,
            "protocol_version": self.protocol_version,
            "persona": self.persona,
            "sections": [
                {
                    "id": s.id,
                    "order": s.order,
                    "text": s.text,
                    "complete": s.complete,
                }
                for s in sorted(self.sections, key=lambda x: (x.order, x.id))
            ],
            "vars": list(self.vars),
        }


# =============================================================================
# Tool Set
# =============================================================================

@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """Individual tool specification.

    Attributes:
        name: Tool name (unique within toolset)
        description: Human-readable description
        parameters: JSON Schema for tool parameters
        digest: Content hash of this definition
        tool_class: Optional classification (e.g., "file", "shell")
    """

    name: str
    description: str
    parameters: dict[str, Any]
    digest: str
    tool_class: str | None = None

    def body_for_digest(self) -> dict[str, Any]:
        """Canonical content body for hashing."""
        body: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }
        if self.tool_class is not None:
            body["tool_class"] = self.tool_class
        return body


@dataclass(frozen=True, slots=True)
class ToolSet:
    """Collection of available tools for an agent.

    Attributes:
        name: Unique artifact name
        semver: Semantic version string
        digest: Content hash
        protocol_version: Schema version
        status: Lifecycle status
        tools: Mapping of tool_name -> ToolDefinition.digest
        definitions: Full tool definitions by name
        metadata: Creator and ownership info
    """

    name: str
    semver: str
    digest: str
    protocol_version: int
    status: ArtifactStatus
    tools: dict[str, str]  # tool_name -> digest
    definitions: dict[str, ToolDefinition] = field(default_factory=dict)
    metadata: ArtifactMetadata | None = None

    def body_for_digest(self) -> dict[str, Any]:
        """Canonical content body for hashing."""
        defs: dict[str, Any] = {}
        for name, td in sorted(self.definitions.items()):
            defs[name] = {
                "name": td.name,
                "description": td.description,
                "parameters": td.parameters,
                "digest": td.digest,
                **({"tool_class": td.tool_class} if td.tool_class is not None else {}),
            }
        return {
            "name": self.name,
            "semver": self.semver,
            "protocol_version": self.protocol_version,
            "tools": dict(sorted(self.tools.items())),
            "definitions": defs,
        }


# =============================================================================
# Sandbox Profile
# =============================================================================

@dataclass(frozen=True, slots=True)
class SandboxProfile:
    """Sandbox execution environment configuration.

    Defines where and how a session runs with security constraints.

    Attributes:
        name: Unique artifact name
        semver: Semantic version
        digest: Content hash
        protocol_version: Schema version
        status: Lifecycle status
        provider: Sandbox provider (none/local_process/local_docker/e2b)
        file_effects: File system access level
        approval_default: Default approval behavior
        sticky_eligible: Whether a sandbox may be kept between turns; a sticky
            harness on a profile that says no is refused at publish and at
            session creation (``require_sticky_compatible``)
        image_digest: Optional container image digest
        browser_model: Commands may call the session's model through the
            browser endpoint (they get a short-lived capability for it). Off
            unless a profile asks: a token every shell can read is one a
            prompt injection can send away.
        metadata: Creator and ownership info
    """

    name: str
    semver: str
    digest: str
    protocol_version: int
    status: ArtifactStatus
    provider: SandboxProviderName
    file_effects: FileEffects = FileEffects.WORKSPACE_WRITE
    approval_default: ApprovalDefault = ApprovalDefault.ASK
    sticky_eligible: bool = True
    image_digest: str | None = None
    browser_model: bool = False
    metadata: ArtifactMetadata | None = None
    runner: str | None = None
    """``provider: runner`` only: which of the tenant's runners hosts the hands."""
    start: str | None = None
    """When a session's sandbox is built: ``with_run`` (as each run begins, alongside the
    first model call, so the first tool does not wait) or ``on_first_tool``. ``None``: the
    deployment's ``SANDBOX_START``."""
    idle_stop_s: int | None = None
    """Stop a sandbox idle this long (saved first; the next tool call restores it); ``0``
    never. ``None``: the deployment's ``SANDBOX_IDLE_STOP_S``."""

    def body_for_digest(self) -> dict[str, Any]:
        """Canonical content body for hashing."""
        body: dict[str, Any] = {
            "name": self.name,
            "semver": self.semver,
            "protocol_version": self.protocol_version,
            "provider": self.provider.value,
            "file_effects": self.file_effects.value,
            "approval_default": self.approval_default.value,
            "sticky_eligible": self.sticky_eligible,
        }
        if self.image_digest is not None:
            body["image_digest"] = self.image_digest
        if self.browser_model:  # only when set, so profiles without it keep their digests
            body["browser_model"] = True
        if self.runner:
            body["runner"] = self.runner
        # Only when set, so profiles without them keep their digests.
        if self.start is not None:
            body["start"] = self.start
        if self.idle_stop_s is not None:
            body["idle_stop_s"] = self.idle_stop_s
        return body


# =============================================================================
# Memory Profile
# =============================================================================

@dataclass(frozen=True, slots=True)
class MemoryProfile:
    """Session memory management configuration.

    Controls context compaction and optional recall backend.

    Attributes:
        name: Unique artifact name
        semver: Semantic version
        digest: Content hash
        protocol_version: Schema version
        status: Lifecycle status
        compaction_enabled: Enable context compaction
        reserve_tokens: Tokens to reserve for output
        keep_recent_tokens: Recent tokens to preserve
        pressure_ratio: Compaction trigger threshold
        context_window: Tokens the pinned model accepts (None: platform default)
        summarization_model: Model for summarization
        recall_enabled: Enable memory recall
        recall_backend: Recall backend type
        recall_mcp_server_url: MCP server for recall
        recall_max_tokens: Most tokens recalled memory may add to a turn (None: 512)
        metadata: Creator and ownership info
    """

    name: str
    semver: str
    digest: str
    protocol_version: int
    status: ArtifactStatus
    compaction_enabled: bool = True
    reserve_tokens: int = 16_384
    keep_recent_tokens: int = 20_000
    pressure_ratio: float = 0.8
    context_window: int | None = None
    summarization_model: str | None = None
    recall_enabled: bool = False
    recall_backend: RecallBackend = RecallBackend.NONE
    recall_mcp_server_url: str | None = None
    recall_max_tokens: int | None = None
    metadata: ArtifactMetadata | None = None

    def body_for_digest(self) -> dict[str, Any]:
        """Canonical content body for hashing."""
        body: dict[str, Any] = {
            "name": self.name,
            "semver": self.semver,
            "protocol_version": self.protocol_version,
            "compaction": {
                "enabled": self.compaction_enabled,
                "reserve_tokens": self.reserve_tokens,
                "keep_recent_tokens": self.keep_recent_tokens,
                "pressure_ratio": self.pressure_ratio,
            },
            "recall": {
                "enabled": self.recall_enabled,
                "backend": self.recall_backend.value,
            },
        }
        if self.summarization_model is not None:
            body["compaction"]["summarization_model"] = self.summarization_model
        if self.context_window is not None:
            body["compaction"]["context_window"] = self.context_window
        if self.recall_mcp_server_url is not None:
            body["recall"]["mcp_server_url"] = self.recall_mcp_server_url
        if self.recall_max_tokens is not None:  # only when set, so profiles without it keep their digests
            body["recall"]["max_tokens"] = self.recall_max_tokens
        return body


# =============================================================================
# Plug Bindings
# =============================================================================

@dataclass(frozen=True, slots=True)
class PlugBindings:
    """Out-of-process service endpoints pinned with composition.

    Attributes:
        gateway_base_url: Model gateway URL
        mcp_server_url: MCP server URL
        acp_base_url: ACP service URL
        model_route: Model routing configuration
    """

    gateway_base_url: str | None = None
    mcp_server_url: str | None = None
    acp_base_url: str | None = None
    model_route: str | None = None

    def body_for_digest(self) -> dict[str, str]:
        """Canonical content body for hashing."""
        out: dict[str, str] = {}
        if self.gateway_base_url:
            out["gateway_base_url"] = self.gateway_base_url
        if self.mcp_server_url:
            out["mcp_server_url"] = self.mcp_server_url
        if self.acp_base_url:
            out["acp_base_url"] = self.acp_base_url
        if self.model_route:
            out["model_route"] = self.model_route
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> PlugBindings | None:
        """Create from dictionary, returns None if empty."""
        if not data:
            return None
        return cls(
            gateway_base_url=data.get("gateway_base_url"),
            mcp_server_url=data.get("mcp_server_url"),
            acp_base_url=data.get("acp_base_url"),
            model_route=data.get("model_route"),
        )


# =============================================================================
# Agent Composition
# =============================================================================

@dataclass(frozen=True, slots=True)
class AgentComposition:
    """Complete agent configuration combining all components.

    References child artifacts by digest for immutable, reproducible builds.

    Attributes:
        name: Unique composition name
        semver: Semantic version
        digest: Content hash
        protocol_version: Schema version
        status: Lifecycle status
        harness_version: Required harness version
        prompt_pack: Reference to PromptPack
        tool_set: Reference to ToolSet
        sandbox_profile: Optional SandboxProfile reference (the session's default hand)
        memory_profile: Optional MemoryProfile reference
        plug_bindings: External service endpoints
        metadata: Creator and ownership info
        hands: Further sandboxes the session may use, by name (each a SandboxProfile)
    """

    name: str
    semver: str
    digest: str
    protocol_version: int
    status: ArtifactStatus
    harness_version: str
    prompt_pack: DigestRef
    tool_set: DigestRef
    sandbox_profile: DigestRef | None = None
    memory_profile: DigestRef | None = None
    plug_bindings: PlugBindings | None = None
    metadata: ArtifactMetadata | None = None
    hands: tuple[tuple[str, DigestRef], ...] = ()

    def body_for_digest(self) -> dict[str, Any]:
        """Canonical content body for hashing."""
        body: dict[str, Any] = {
            "name": self.name,
            "semver": self.semver,
            "protocol_version": self.protocol_version,
            "harness_version": self.harness_version,
            "prompt_pack": self.prompt_pack.to_dict(),
            "tool_set": self.tool_set.to_dict(),
        }
        if self.sandbox_profile is not None:
            body["sandbox_profile"] = self.sandbox_profile.to_dict()
        if self.memory_profile is not None:
            body["memory_profile"] = self.memory_profile.to_dict()
        if self.plug_bindings is not None:
            pb = self.plug_bindings.body_for_digest()
            if pb:
                body["plug_bindings"] = pb
        if self.hands:  # only when set: compositions without hands keep their digests
            body["hands"] = {name: ref.to_dict() for name, ref in sorted(self.hands)}
        return body


# =============================================================================
# Union Type
# =============================================================================

Artifact = PromptPack | ToolSet | AgentComposition | SandboxProfile | MemoryProfile


# =============================================================================
# Session Pins
# =============================================================================

@dataclass(frozen=True, slots=True)
class SessionPins:
    """Durable create-time pins for a session.

    Captures exact artifact digests at session creation for reproducibility.
    Stored in session row and never re-resolved.

    Attributes:
        composition_id: Composition name
        composition_digest: Composition content hash
        harness_version: Harness version at creation
        prompt_pack_id: PromptPack name
        prompt_pack_digest: PromptPack hash
        tool_set_id: ToolSet name
        tool_set_digest: ToolSet hash
        sandbox_profile_id: Optional SandboxProfile name
        sandbox_profile_digest: Optional SandboxProfile hash
        memory_profile_id: Optional MemoryProfile name
        memory_profile_digest: Optional MemoryProfile hash
        gateway_base_url: Model gateway URL
        mcp_server_url: MCP server URL
        acp_base_url: ACP service URL
        model_route: Model routing config
    """

    composition_id: str
    composition_digest: str
    harness_version: str
    prompt_pack_id: str
    prompt_pack_digest: str
    tool_set_id: str
    tool_set_digest: str
    sandbox_profile_id: str | None = None
    sandbox_profile_digest: str | None = None
    memory_profile_id: str | None = None
    memory_profile_digest: str | None = None
    gateway_base_url: str | None = None
    mcp_server_url: str | None = None
    acp_base_url: str | None = None
    model_route: str | None = None
    hands: tuple[tuple[str, str, str], ...] = ()
    """Named hands beyond the default: (name, sandbox profile id, digest)."""

    def refs(self) -> Iterator[tuple[ArtifactKind, str, str]]:
        """Every artifact pinned, as ``(kind, id, digest)``: the composition, its parts, its hands."""
        yield ArtifactKind.COMPOSITION, self.composition_id, self.composition_digest
        yield ArtifactKind.PROMPT_PACK, self.prompt_pack_id, self.prompt_pack_digest
        yield ArtifactKind.TOOL_SET, self.tool_set_id, self.tool_set_digest
        if self.sandbox_profile_id and self.sandbox_profile_digest:
            yield ArtifactKind.SANDBOX_PROFILE, self.sandbox_profile_id, self.sandbox_profile_digest
        for _, profile_id, digest in self.hands:
            yield ArtifactKind.SANDBOX_PROFILE, profile_id, digest
        if self.memory_profile_id and self.memory_profile_digest:
            yield ArtifactKind.MEMORY_PROFILE, self.memory_profile_id, self.memory_profile_digest

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        data: dict[str, Any] = {
            "composition_id": self.composition_id,
            "composition_digest": self.composition_digest,
            "harness_version": self.harness_version,
            "prompt_pack_id": self.prompt_pack_id,
            "prompt_pack_digest": self.prompt_pack_digest,
            "tool_set_id": self.tool_set_id,
            "tool_set_digest": self.tool_set_digest,
        }
        if self.sandbox_profile_id is not None:
            data["sandbox_profile_id"] = self.sandbox_profile_id
        if self.sandbox_profile_digest is not None:
            data["sandbox_profile_digest"] = self.sandbox_profile_digest
        if self.memory_profile_id is not None:
            data["memory_profile_id"] = self.memory_profile_id
        if self.memory_profile_digest is not None:
            data["memory_profile_digest"] = self.memory_profile_digest
        if self.gateway_base_url is not None:
            data["gateway_base_url"] = self.gateway_base_url
        if self.mcp_server_url is not None:
            data["mcp_server_url"] = self.mcp_server_url
        if self.acp_base_url is not None:
            data["acp_base_url"] = self.acp_base_url
        if self.model_route is not None:
            data["model_route"] = self.model_route
        if self.hands:
            data["hands"] = {name: {"id": pid, "digest": d} for name, pid, d in self.hands}
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionPins:
        """Create from dictionary."""
        return cls(
            composition_id=str(data["composition_id"]),
            composition_digest=str(data["composition_digest"]),
            harness_version=str(data["harness_version"]),
            prompt_pack_id=str(data["prompt_pack_id"]),
            prompt_pack_digest=str(data["prompt_pack_digest"]),
            tool_set_id=str(data["tool_set_id"]),
            tool_set_digest=str(data["tool_set_digest"]),
            sandbox_profile_id=data.get("sandbox_profile_id"),
            sandbox_profile_digest=data.get("sandbox_profile_digest"),
            memory_profile_id=data.get("memory_profile_id"),
            memory_profile_digest=data.get("memory_profile_digest"),
            gateway_base_url=data.get("gateway_base_url"),
            mcp_server_url=data.get("mcp_server_url"),
            acp_base_url=data.get("acp_base_url"),
            model_route=data.get("model_route"),
            hands=tuple(
                (str(name), str(ref["id"]), str(ref["digest"]))
                for name, ref in sorted((data.get("hands") or {}).items())
            ),
        )


# =============================================================================
# Usage
# =============================================================================

@dataclass(frozen=True, slots=True)
class ArtifactUsageStats:
    """How many sessions pin an artifact, read from the sessions' own pins.

    The sessions table is the record of use: each session stores the digests it
    pinned (``SessionPins``), so nothing is counted twice and nothing drifts.
    ``versions`` says which digests sessions still pin, newest use first: a version
    no session pins can be deprecated without stranding anyone.
    """

    artifact_kind: ArtifactKind
    artifact_id: str
    artifact_digest: str | None
    """The one version counted, or ``None`` for every version."""
    session_count: int
    active_sessions: int
    """Of those, sessions not yet ended (they keep their pins until they end)."""
    unique_principals: int
    first_used_at: datetime | None
    last_used_at: datetime | None
    versions: tuple[tuple[str, int], ...] = ()
    """``(digest, sessions)`` for each pinned version, most recently used first."""

    @classmethod
    def empty(cls, kind: ArtifactKind, artifact_id: str, digest: str | None = None) -> ArtifactUsageStats:
        return cls(
            artifact_kind=kind,
            artifact_id=artifact_id,
            artifact_digest=digest,
            session_count=0,
            active_sessions=0,
            unique_principals=0,
            first_used_at=None,
            last_used_at=None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_kind": self.artifact_kind.value,
            "artifact_id": self.artifact_id,
            "artifact_digest": self.artifact_digest,
            "session_count": self.session_count,
            "active_sessions": self.active_sessions,
            "unique_principals": self.unique_principals,
            "first_used_at": self.first_used_at.isoformat() if self.first_used_at else None,
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
            "versions": [{"digest": d, "sessions": n} for d, n in self.versions],
        }
