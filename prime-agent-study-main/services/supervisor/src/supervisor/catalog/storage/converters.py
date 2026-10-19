"""Artifact converters — dict <-> dataclass transformations.

Clean conversion functions with proper typing:
- *_from_dict: Parse dicts into frozen dataclasses
- *_to_dict: Serialize dataclasses to dicts
- with_*: Create modified copies of frozen artifacts

Design principles:
- No `# type: ignore` - use proper type coercion
- All functions are pure (no side effects)
- Proper validation of required fields
- Optional fields have sensible defaults

Reference: Extracted from store.py with improvements.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Any

from supervisor.catalog.core.digests import content_digest, is_unset_digest
from supervisor.catalog.core.errors import DigestMismatch
from supervisor.catalog.core.types import (
    AgentComposition,
    ApprovalDefault,
    # Type alias
    Artifact,
    # Enums
    ArtifactKind,
    # Dataclasses
    ArtifactMetadata,
    ArtifactStatus,
    DigestRef,
    FileEffects,
    MemoryProfile,
    PlugBindings,
    PromptPack,
    PromptSection,
    RecallBackend,
    SandboxProfile,
    SandboxProviderName,
    ToolDefinition,
    ToolSet,
)

__all__ = [
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
    "sandbox_profile_from_dict",
    "sandbox_profile_to_dict",
    "tool_set_from_dict",
    "tool_set_to_dict",
    "with_digest",
    "with_status",
]


# =============================================================================
# Metadata Converters
# =============================================================================

def metadata_from_dict(data: dict[str, Any] | None) -> ArtifactMetadata | None:
    """Parse metadata from dict.

    Args:
        data: Dict with metadata fields or None

    Returns:
        ArtifactMetadata or None if data is None/empty
    """
    if not data:
        return None

    created_at = data.get("created_at")
    if isinstance(created_at, str):
        created_at = datetime.fromisoformat(created_at)
    elif created_at is None:
        created_at = datetime.now()

    updated_at = data.get("updated_at")
    if isinstance(updated_at, str):
        updated_at = datetime.fromisoformat(updated_at)
    elif updated_at is None:
        updated_at = created_at

    return ArtifactMetadata(
        created_by=data.get("created_by", "unknown"),
        created_at=created_at,
        updated_at=updated_at,
        owner_id=data.get("owner_id"),
    )


def metadata_to_dict(metadata: ArtifactMetadata | None) -> dict[str, Any] | None:
    """Serialize metadata to dict.

    Args:
        metadata: ArtifactMetadata or None

    Returns:
        Dict representation or None
    """
    if metadata is None:
        return None

    return {
        "created_by": metadata.created_by,
        "created_at": metadata.created_at.isoformat(),
        "updated_at": metadata.updated_at.isoformat(),
        "owner_id": metadata.owner_id,
    }


# =============================================================================
# PromptPack Converters
# =============================================================================

def prompt_pack_from_dict(data: dict[str, Any]) -> PromptPack:
    """Parse PromptPack from dict.

    Args:
        data: Dict with prompt pack fields

    Returns:
        Frozen PromptPack instance
    """
    sections = tuple(
        PromptSection(
            id=s["id"],
            order=int(s["order"]),
            text=s["text"],
            complete=bool(s.get("complete", True)),
        )
        for s in data.get("sections", [])
    )

    # Parse status with proper enum coercion
    status_str = data.get("status", "offered")
    status = ArtifactStatus(status_str) if status_str else ArtifactStatus.OFFERED

    return PromptPack(
        name=data["name"],
        semver=data["semver"],
        digest=data.get("digest", ""),
        protocol_version=int(data.get("protocol_version", 1)),
        status=status,
        persona=data.get("persona"),
        sections=sections,
        vars=tuple(data.get("vars", [])),
        metadata=metadata_from_dict(data.get("metadata")),
    )


def prompt_pack_to_dict(pack: PromptPack) -> dict[str, Any]:
    """Serialize PromptPack to dict.

    Args:
        pack: PromptPack instance

    Returns:
        Dict representation
    """
    return {
        "name": pack.name,
        "semver": pack.semver,
        "digest": pack.digest,
        "protocol_version": pack.protocol_version,
        "status": pack.status.value if isinstance(pack.status, ArtifactStatus) else pack.status,
        "persona": pack.persona,
        "sections": [
            {
                "id": s.id,
                "order": s.order,
                "text": s.text,
                "complete": s.complete,
            }
            for s in pack.sections
        ],
        "vars": list(pack.vars),
        "metadata": metadata_to_dict(pack.metadata),
    }


# =============================================================================
# ToolSet Converters
# =============================================================================

def _tool_definition_from_dict(raw: dict[str, Any]) -> ToolDefinition:
    """Parse one tool, computing its digest or verifying a claimed one."""
    tool = ToolDefinition(
        name=raw["name"],
        description=raw["description"],
        parameters=dict(raw["parameters"]),
        digest="",
        tool_class=raw.get("tool_class"),
    )
    actual = content_digest(tool.body_for_digest())
    claimed = raw.get("digest")
    if not is_unset_digest(claimed) and claimed != actual:
        raise DigestMismatch(expected=claimed, actual=actual)
    return replace(tool, digest=actual)


def tool_set_from_dict(data: dict[str, Any]) -> ToolSet:
    """Parse ToolSet from dict.

    Args:
        data: Dict with tool set fields

    Returns:
        Frozen ToolSet instance
    """
    definitions = {
        name: _tool_definition_from_dict(raw) for name, raw in (data.get("definitions") or {}).items()
    }

    status_str = data.get("status", "offered")
    status = ArtifactStatus(status_str) if status_str else ArtifactStatus.OFFERED

    return ToolSet(
        name=data["name"],
        semver=data["semver"],
        digest=data.get("digest", ""),
        protocol_version=int(data.get("protocol_version", 1)),
        status=status,
        # Derived, never trusted from input: each tool name pins its verified definition digest.
        tools={name: td.digest for name, td in definitions.items()},
        definitions=definitions,
        metadata=metadata_from_dict(data.get("metadata")),
    )


def tool_set_to_dict(toolset: ToolSet) -> dict[str, Any]:
    """Serialize ToolSet to dict.

    Args:
        toolset: ToolSet instance

    Returns:
        Dict representation
    """
    return {
        "name": toolset.name,
        "semver": toolset.semver,
        "digest": toolset.digest,
        "protocol_version": toolset.protocol_version,
        "status": toolset.status.value if isinstance(toolset.status, ArtifactStatus) else toolset.status,
        "tools": dict(toolset.tools),
        "definitions": {
            name: {
                "name": defn.name,
                "description": defn.description,
                "parameters": dict(defn.parameters),
                "digest": defn.digest,
                "tool_class": defn.tool_class,
            }
            for name, defn in toolset.definitions.items()
        },
        "metadata": metadata_to_dict(toolset.metadata),
    }


# =============================================================================
# AgentComposition Converters
# =============================================================================

def composition_from_dict(data: dict[str, Any]) -> AgentComposition:
    """Parse AgentComposition from dict.

    Args:
        data: Dict with composition fields

    Returns:
        Frozen AgentComposition instance
    """
    pp = data["prompt_pack"]
    ts = data["tool_set"]

    sp = data.get("sandbox_profile")
    sandbox_ref = (
        DigestRef(id=sp["id"], digest=sp["digest"]) if sp is not None else None
    )

    mp = data.get("memory_profile")
    memory_ref = (
        DigestRef(id=mp["id"], digest=mp["digest"]) if mp is not None else None
    )

    status_str = data.get("status", "offered")
    status = ArtifactStatus(status_str) if status_str else ArtifactStatus.OFFERED

    return AgentComposition(
        name=data["name"],
        semver=data["semver"],
        digest=data.get("digest", ""),
        protocol_version=int(data.get("protocol_version", 1)),
        status=status,
        harness_version=data["harness_version"],
        prompt_pack=DigestRef(id=pp["id"], digest=pp["digest"]),
        tool_set=DigestRef(id=ts["id"], digest=ts["digest"]),
        sandbox_profile=sandbox_ref,
        memory_profile=memory_ref,
        plug_bindings=PlugBindings.from_dict(data.get("plug_bindings")),
        metadata=metadata_from_dict(data.get("metadata")),
    )


def composition_to_dict(comp: AgentComposition) -> dict[str, Any]:
    """Serialize AgentComposition to dict.

    Args:
        comp: AgentComposition instance

    Returns:
        Dict representation
    """
    result: dict[str, Any] = {
        "name": comp.name,
        "semver": comp.semver,
        "digest": comp.digest,
        "protocol_version": comp.protocol_version,
        "status": comp.status.value if isinstance(comp.status, ArtifactStatus) else comp.status,
        "harness_version": comp.harness_version,
        "prompt_pack": {"id": comp.prompt_pack.id, "digest": comp.prompt_pack.digest},
        "tool_set": {"id": comp.tool_set.id, "digest": comp.tool_set.digest},
        "metadata": metadata_to_dict(comp.metadata),
    }

    if comp.sandbox_profile is not None:
        result["sandbox_profile"] = {
            "id": comp.sandbox_profile.id,
            "digest": comp.sandbox_profile.digest,
        }

    if comp.memory_profile is not None:
        result["memory_profile"] = {
            "id": comp.memory_profile.id,
            "digest": comp.memory_profile.digest,
        }

    if comp.plug_bindings is not None:
        result["plug_bindings"] = comp.plug_bindings.body_for_digest()  # its serial form; from_dict reads it back

    return result


# =============================================================================
# SandboxProfile Converters
# =============================================================================

def sandbox_profile_from_dict(data: dict[str, Any]) -> SandboxProfile:
    """Parse SandboxProfile from dict.

    Args:
        data: Dict with sandbox profile fields

    Returns:
        Frozen SandboxProfile instance
    """
    status_str = data.get("status", "offered")
    status = ArtifactStatus(status_str) if status_str else ArtifactStatus.OFFERED

    provider_str = data.get("provider", "local_process")
    provider = SandboxProviderName(provider_str) if provider_str else SandboxProviderName.LOCAL_PROCESS

    file_effects_str = data.get("file_effects", "workspace_write")
    file_effects = FileEffects(file_effects_str) if file_effects_str else FileEffects.WORKSPACE_WRITE

    approval_str = data.get("approval_default", "ask")
    approval_default = ApprovalDefault(approval_str) if approval_str else ApprovalDefault.ASK

    return SandboxProfile(
        name=data["name"],
        semver=data["semver"],
        digest=data.get("digest", ""),
        protocol_version=int(data.get("protocol_version", 1)),
        status=status,
        provider=provider,
        file_effects=file_effects,
        approval_default=approval_default,
        sticky_eligible=bool(data.get("sticky_eligible", True)),
        image_digest=data.get("image_digest"),
        browser_model=bool(data.get("browser_model", False)),
        metadata=metadata_from_dict(data.get("metadata")),
    )


def sandbox_profile_to_dict(profile: SandboxProfile) -> dict[str, Any]:
    """Serialize SandboxProfile to dict.

    Args:
        profile: SandboxProfile instance

    Returns:
        Dict representation
    """
    return {
        "name": profile.name,
        "semver": profile.semver,
        "digest": profile.digest,
        "protocol_version": profile.protocol_version,
        "status": profile.status.value if isinstance(profile.status, ArtifactStatus) else profile.status,
        "provider": profile.provider.value if isinstance(profile.provider, SandboxProviderName) else profile.provider,
        "file_effects": profile.file_effects.value if isinstance(profile.file_effects, FileEffects) else profile.file_effects,
        "approval_default": profile.approval_default.value if isinstance(profile.approval_default, ApprovalDefault) else profile.approval_default,
        "sticky_eligible": profile.sticky_eligible,
        "image_digest": profile.image_digest,
        "browser_model": profile.browser_model,
        "metadata": metadata_to_dict(profile.metadata),
    }


# =============================================================================
# MemoryProfile Converters
# =============================================================================

def memory_profile_from_dict(data: dict[str, Any]) -> MemoryProfile:
    """Parse MemoryProfile from dict.

    Args:
        data: Dict with memory profile fields

    Returns:
        Frozen MemoryProfile instance
    """
    compaction = data.get("compaction") or {}
    recall = data.get("recall") or {}

    status_str = data.get("status", "offered")
    status = ArtifactStatus(status_str) if status_str else ArtifactStatus.OFFERED

    recall_backend_str = recall.get("backend", "none")
    recall_backend = RecallBackend(recall_backend_str) if recall_backend_str else RecallBackend.NONE

    return MemoryProfile(
        name=data["name"],
        semver=data["semver"],
        digest=data.get("digest", ""),
        protocol_version=int(data.get("protocol_version", 1)),
        status=status,
        compaction_enabled=bool(compaction.get("enabled", True)),
        reserve_tokens=int(compaction.get("reserve_tokens", 16_384)),
        keep_recent_tokens=int(compaction.get("keep_recent_tokens", 20_000)),
        pressure_ratio=float(compaction.get("pressure_ratio", 0.8)),
        context_window=(
            int(compaction["context_window"])
            if compaction.get("context_window") is not None
            else None
        ),
        summarization_model=compaction.get("summarization_model"),
        recall_enabled=bool(recall.get("enabled", False)),
        recall_backend=recall_backend,
        recall_mcp_server_url=recall.get("mcp_server_url"),
        metadata=metadata_from_dict(data.get("metadata")),
    )


def memory_profile_to_dict(profile: MemoryProfile) -> dict[str, Any]:
    """Serialize MemoryProfile to dict.

    Args:
        profile: MemoryProfile instance

    Returns:
        Dict representation
    """
    return {
        "name": profile.name,
        "semver": profile.semver,
        "digest": profile.digest,
        "protocol_version": profile.protocol_version,
        "status": profile.status.value if isinstance(profile.status, ArtifactStatus) else profile.status,
        "compaction": {
            "enabled": profile.compaction_enabled,
            "reserve_tokens": profile.reserve_tokens,
            "keep_recent_tokens": profile.keep_recent_tokens,
            "pressure_ratio": profile.pressure_ratio,
            "context_window": profile.context_window,
            "summarization_model": profile.summarization_model,
        },
        "recall": {
            "enabled": profile.recall_enabled,
            "backend": profile.recall_backend.value if isinstance(profile.recall_backend, RecallBackend) else profile.recall_backend,
            "mcp_server_url": profile.recall_mcp_server_url,
        },
        "metadata": metadata_to_dict(profile.metadata),
    }


# =============================================================================
# SessionPins Converters
# =============================================================================

# =============================================================================
# Artifact Helpers
# =============================================================================

def kind_of(artifact: Artifact) -> ArtifactKind:
    """Get the kind of an artifact.

    Args:
        artifact: Any artifact instance

    Returns:
        ArtifactKind enum value

    Raises:
        TypeError: If artifact type is unknown
    """
    if isinstance(artifact, PromptPack):
        return ArtifactKind.PROMPT_PACK
    if isinstance(artifact, ToolSet):
        return ArtifactKind.TOOL_SET
    if isinstance(artifact, AgentComposition):
        return ArtifactKind.COMPOSITION
    if isinstance(artifact, SandboxProfile):
        return ArtifactKind.SANDBOX_PROFILE
    if isinstance(artifact, MemoryProfile):
        return ArtifactKind.MEMORY_PROFILE
    raise TypeError(f"unknown artifact type {type(artifact)!r}")


def with_digest(artifact: Artifact, digest: str) -> Artifact:
    """Create a copy of artifact with new digest.

    Since artifacts are frozen, this creates a new instance.

    Args:
        artifact: Source artifact
        digest: New digest value

    Returns:
        New artifact with updated digest
    """
    if isinstance(artifact, PromptPack):
        return PromptPack(
            name=artifact.name,
            semver=artifact.semver,
            digest=digest,
            protocol_version=artifact.protocol_version,
            status=artifact.status,
            persona=artifact.persona,
            sections=artifact.sections,
            vars=artifact.vars,
            metadata=artifact.metadata,
        )
    if isinstance(artifact, ToolSet):
        return ToolSet(
            name=artifact.name,
            semver=artifact.semver,
            digest=digest,
            protocol_version=artifact.protocol_version,
            status=artifact.status,
            tools=artifact.tools,
            definitions=artifact.definitions,
            metadata=artifact.metadata,
        )
    if isinstance(artifact, SandboxProfile):
        return replace(artifact, digest=digest)
    if isinstance(artifact, MemoryProfile):
        return replace(artifact, digest=digest)
    if isinstance(artifact, AgentComposition):
        return AgentComposition(
            name=artifact.name,
            semver=artifact.semver,
            digest=digest,
            protocol_version=artifact.protocol_version,
            status=artifact.status,
            harness_version=artifact.harness_version,
            prompt_pack=artifact.prompt_pack,
            tool_set=artifact.tool_set,
            sandbox_profile=artifact.sandbox_profile,
            memory_profile=artifact.memory_profile,
            plug_bindings=artifact.plug_bindings,
            metadata=artifact.metadata,
        )
    raise TypeError(f"unknown artifact type {type(artifact)!r}")


def with_status(artifact: Artifact, status: ArtifactStatus) -> Artifact:
    """Create a copy of artifact with new status.

    Since artifacts are frozen, this creates a new instance.

    Args:
        artifact: Source artifact
        status: New status value

    Returns:
        New artifact with updated status
    """
    if isinstance(artifact, PromptPack):
        return PromptPack(
            name=artifact.name,
            semver=artifact.semver,
            digest=artifact.digest,
            protocol_version=artifact.protocol_version,
            status=status,
            persona=artifact.persona,
            sections=artifact.sections,
            vars=artifact.vars,
            metadata=artifact.metadata,
        )
    if isinstance(artifact, ToolSet):
        return ToolSet(
            name=artifact.name,
            semver=artifact.semver,
            digest=artifact.digest,
            protocol_version=artifact.protocol_version,
            status=status,
            tools=artifact.tools,
            definitions=artifact.definitions,
            metadata=artifact.metadata,
        )
    if isinstance(artifact, SandboxProfile):
        return replace(artifact, status=status)
    if isinstance(artifact, MemoryProfile):
        return replace(artifact, status=status)
    if isinstance(artifact, AgentComposition):
        return AgentComposition(
            name=artifact.name,
            semver=artifact.semver,
            digest=artifact.digest,
            protocol_version=artifact.protocol_version,
            status=status,
            harness_version=artifact.harness_version,
            prompt_pack=artifact.prompt_pack,
            tool_set=artifact.tool_set,
            sandbox_profile=artifact.sandbox_profile,
            memory_profile=artifact.memory_profile,
            plug_bindings=artifact.plug_bindings,
            metadata=artifact.metadata,
        )
    raise TypeError(f"unknown artifact type {type(artifact)!r}")


# =============================================================================
# Generic Converters
# =============================================================================

def artifact_from_dict(kind: ArtifactKind, data: dict[str, Any]) -> Artifact:
    """Parse artifact from dict by kind.

    Args:
        kind: Type of artifact
        data: Dict with artifact fields

    Returns:
        Appropriate artifact instance

    Raises:
        ValueError: If kind is unknown
    """
    converters = {
        ArtifactKind.PROMPT_PACK: prompt_pack_from_dict,
        ArtifactKind.TOOL_SET: tool_set_from_dict,
        ArtifactKind.COMPOSITION: composition_from_dict,
        ArtifactKind.SANDBOX_PROFILE: sandbox_profile_from_dict,
        ArtifactKind.MEMORY_PROFILE: memory_profile_from_dict,
    }
    converter = converters.get(kind)
    if converter is None:
        raise ValueError(f"unknown artifact kind: {kind}")
    return converter(data)


def artifact_to_dict(artifact: Artifact) -> dict[str, Any]:
    """Serialize artifact to dict.

    Args:
        artifact: Any artifact instance

    Returns:
        Dict representation

    Raises:
        TypeError: If artifact type is unknown
    """
    if isinstance(artifact, PromptPack):
        return prompt_pack_to_dict(artifact)
    if isinstance(artifact, ToolSet):
        return tool_set_to_dict(artifact)
    if isinstance(artifact, AgentComposition):
        return composition_to_dict(artifact)
    if isinstance(artifact, SandboxProfile):
        return sandbox_profile_to_dict(artifact)
    if isinstance(artifact, MemoryProfile):
        return memory_profile_to_dict(artifact)
    raise TypeError(f"unknown artifact type {type(artifact)!r}")
