"""Artifacts to and from their JSON form (published bodies, the database, the shipped files).

``*_from_dict`` parses leniently (absent optional fields take their defaults;
``check_publishable`` is what refuses unknown keys), and ``artifact_to_dict`` writes
the form ``from_dict`` reads back. The body is content only: ``metadata`` (who
published it, when) is assigned by the catalog from its database row and never
read from a body, so a client cannot claim to be someone else.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any

from supervisor.catalog.core.digests import content_digest, is_unset_digest
from supervisor.catalog.core.errors import DigestMismatch
from supervisor.catalog.core.types import (
    DEFAULT_PROTOCOL_VERSION,
    AgentComposition,
    ApprovalDefault,
    Artifact,
    ArtifactKind,
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
    "kind_of",
    "memory_profile_from_dict",
    "prompt_pack_from_dict",
    "sandbox_profile_from_dict",
    "tool_set_from_dict",
    "with_digest",
    "with_metadata",
    "with_status",
]


def _status(data: dict[str, Any]) -> ArtifactStatus:
    return ArtifactStatus(data.get("status") or ArtifactStatus.OFFERED)


def _protocol(data: dict[str, Any]) -> int:
    return int(data.get("protocol_version", DEFAULT_PROTOCOL_VERSION))


def _header(artifact: Artifact) -> dict[str, Any]:
    """The fields every kind shares, in its serial form."""
    return {
        "name": artifact.name,
        "semver": artifact.semver,
        "digest": artifact.digest,
        "protocol_version": artifact.protocol_version,
        "status": artifact.status.value,
    }


def _ref(raw: dict[str, Any] | None) -> DigestRef | None:
    return DigestRef.from_dict(raw) if raw is not None else None


# --- prompt pack -----------------------------------------------------------------------------


def prompt_pack_from_dict(data: dict[str, Any]) -> PromptPack:
    return PromptPack(
        name=data["name"],
        semver=data["semver"],
        digest=data.get("digest", ""),
        protocol_version=_protocol(data),
        status=_status(data),
        persona=data.get("persona"),
        sections=tuple(
            PromptSection(
                id=s["id"],
                order=int(s["order"]),
                text=s["text"],
                complete=bool(s.get("complete", True)),
            )
            for s in data.get("sections", [])
        ),
        vars=tuple(data.get("vars", [])),
    )


def _prompt_pack_to_dict(pack: PromptPack) -> dict[str, Any]:
    return {
        **_header(pack),
        "persona": pack.persona,
        "sections": [
            {"id": s.id, "order": s.order, "text": s.text, "complete": s.complete}
            for s in pack.sections
        ],
        "vars": list(pack.vars),
    }


# --- tool set --------------------------------------------------------------------------------


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
        raise DigestMismatch(expected=str(claimed), actual=actual)
    return replace(tool, digest=actual)


def tool_set_from_dict(data: dict[str, Any]) -> ToolSet:
    definitions = {
        name: _tool_definition_from_dict(raw) for name, raw in (data.get("definitions") or {}).items()
    }
    return ToolSet(
        name=data["name"],
        semver=data["semver"],
        digest=data.get("digest", ""),
        protocol_version=_protocol(data),
        status=_status(data),
        # Derived, never trusted from input: each tool name pins its verified definition digest.
        tools={name: td.digest for name, td in definitions.items()},
        definitions=definitions,
    )


def _tool_set_to_dict(tool_set: ToolSet) -> dict[str, Any]:
    return {
        **_header(tool_set),
        "tools": dict(tool_set.tools),
        "definitions": {
            name: {
                "name": td.name,
                "description": td.description,
                "parameters": dict(td.parameters),
                "digest": td.digest,
                "tool_class": td.tool_class,
            }
            for name, td in tool_set.definitions.items()
        },
    }


# --- composition -----------------------------------------------------------------------------


def composition_from_dict(data: dict[str, Any]) -> AgentComposition:
    return AgentComposition(
        name=data["name"],
        semver=data["semver"],
        digest=data.get("digest", ""),
        protocol_version=_protocol(data),
        status=_status(data),
        harness_version=data["harness_version"],
        prompt_pack=DigestRef.from_dict(data["prompt_pack"]),
        tool_set=DigestRef.from_dict(data["tool_set"]),
        sandbox_profile=_ref(data.get("sandbox_profile")),
        memory_profile=_ref(data.get("memory_profile")),
        plug_bindings=PlugBindings.from_dict(data.get("plug_bindings")),
        hands=tuple(
            (str(name), DigestRef.from_dict(ref))
            for name, ref in sorted((data.get("hands") or {}).items())
        ),
    )


def _composition_to_dict(comp: AgentComposition) -> dict[str, Any]:
    out: dict[str, Any] = {
        **_header(comp),
        "harness_version": comp.harness_version,
        "prompt_pack": comp.prompt_pack.to_dict(),
        "tool_set": comp.tool_set.to_dict(),
    }
    if comp.sandbox_profile is not None:
        out["sandbox_profile"] = comp.sandbox_profile.to_dict()
    if comp.memory_profile is not None:
        out["memory_profile"] = comp.memory_profile.to_dict()
    if comp.plug_bindings is not None:
        out["plug_bindings"] = comp.plug_bindings.body_for_digest()  # its serial form; from_dict reads it back
    if comp.hands:
        out["hands"] = {name: ref.to_dict() for name, ref in comp.hands}
    return out


# --- sandbox profile -------------------------------------------------------------------------


def sandbox_profile_from_dict(data: dict[str, Any]) -> SandboxProfile:
    idle = data.get("idle_stop_s")
    return SandboxProfile(
        name=data["name"],
        semver=data["semver"],
        digest=data.get("digest", ""),
        protocol_version=_protocol(data),
        status=_status(data),
        provider=SandboxProviderName(data.get("provider") or SandboxProviderName.LOCAL_PROCESS),
        file_effects=FileEffects(data.get("file_effects") or FileEffects.WORKSPACE_WRITE),
        approval_default=ApprovalDefault(data.get("approval_default") or ApprovalDefault.ASK),
        sticky_eligible=bool(data.get("sticky_eligible", True)),
        image_digest=data.get("image_digest"),
        browser_model=bool(data.get("browser_model", False)),
        runner=data.get("runner") or None,
        start=data.get("start"),
        idle_stop_s=int(idle) if idle is not None else None,
    )


def _sandbox_profile_to_dict(profile: SandboxProfile) -> dict[str, Any]:
    return {
        **_header(profile),
        "provider": profile.provider.value,
        "file_effects": profile.file_effects.value,
        "approval_default": profile.approval_default.value,
        "sticky_eligible": profile.sticky_eligible,
        "image_digest": profile.image_digest,
        "browser_model": profile.browser_model,
        **({"runner": profile.runner} if profile.runner else {}),
        **({"start": profile.start} if profile.start is not None else {}),
        **({"idle_stop_s": profile.idle_stop_s} if profile.idle_stop_s is not None else {}),
    }


# --- memory profile --------------------------------------------------------------------------


def memory_profile_from_dict(data: dict[str, Any]) -> MemoryProfile:
    compaction = data.get("compaction") or {}
    recall = data.get("recall") or {}
    window = compaction.get("context_window")
    recall_max = recall.get("max_tokens")
    return MemoryProfile(
        name=data["name"],
        semver=data["semver"],
        digest=data.get("digest", ""),
        protocol_version=_protocol(data),
        status=_status(data),
        compaction_enabled=bool(compaction.get("enabled", True)),
        reserve_tokens=int(compaction.get("reserve_tokens", 16_384)),
        keep_recent_tokens=int(compaction.get("keep_recent_tokens", 20_000)),
        pressure_ratio=float(compaction.get("pressure_ratio", 0.8)),
        context_window=int(window) if window is not None else None,
        summarization_model=compaction.get("summarization_model"),
        recall_enabled=bool(recall.get("enabled", False)),
        recall_backend=RecallBackend(recall.get("backend") or RecallBackend.NONE),
        recall_mcp_server_url=recall.get("mcp_server_url"),
        recall_max_tokens=int(recall_max) if recall_max is not None else None,
    )


def _memory_profile_to_dict(profile: MemoryProfile) -> dict[str, Any]:
    return {
        **_header(profile),
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
            "backend": profile.recall_backend.value,
            "mcp_server_url": profile.recall_mcp_server_url,
            **({"max_tokens": profile.recall_max_tokens} if profile.recall_max_tokens is not None else {}),
        },
    }


# --- any kind --------------------------------------------------------------------------------

_KINDS: dict[ArtifactKind, tuple[type, Callable[[dict[str, Any]], Any], Callable[[Any], dict[str, Any]]]] = {
    ArtifactKind.PROMPT_PACK: (PromptPack, prompt_pack_from_dict, _prompt_pack_to_dict),
    ArtifactKind.TOOL_SET: (ToolSet, tool_set_from_dict, _tool_set_to_dict),
    ArtifactKind.COMPOSITION: (AgentComposition, composition_from_dict, _composition_to_dict),
    ArtifactKind.SANDBOX_PROFILE: (SandboxProfile, sandbox_profile_from_dict, _sandbox_profile_to_dict),
    ArtifactKind.MEMORY_PROFILE: (MemoryProfile, memory_profile_from_dict, _memory_profile_to_dict),
}


def kind_of(artifact: Artifact) -> ArtifactKind:
    for kind, (cls, _, _) in _KINDS.items():
        if isinstance(artifact, cls):
            return kind
    raise TypeError(f"unknown artifact type {type(artifact)!r}")


def artifact_from_dict(kind: ArtifactKind | str, data: dict[str, Any]) -> Artifact:
    """Parse ``data`` as an artifact of ``kind``; ``ValueError`` for an unknown kind."""
    try:
        _, parse, _ = _KINDS[ArtifactKind(kind)]
    except ValueError:
        raise ValueError(f"unknown artifact kind: {kind}") from None
    artifact: Artifact = parse(data)
    return artifact


def artifact_to_dict(artifact: Artifact) -> dict[str, Any]:
    """The artifact's serial form (content, digest and status; not its metadata)."""
    _, _, write = _KINDS[kind_of(artifact)]
    return write(artifact)


def with_digest(artifact: Artifact, digest: str) -> Artifact:
    return replace(artifact, digest=digest)


def with_status(artifact: Artifact, status: ArtifactStatus) -> Artifact:
    return replace(artifact, status=status)


def with_metadata(artifact: Artifact, metadata: ArtifactMetadata | None) -> Artifact:
    return replace(artifact, metadata=metadata)
