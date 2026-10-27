"""What a session runs, read back from its pins: each pinned part, at its exact version.

A session's pins are digests; this names what they are (version, status, the
settings that shape a run) so a person can see what the session is running and
whether any of it has been deprecated since. Internal service addresses are
reported as configured or not, never shown.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from supervisor.catalog.core.errors import CatalogError
from supervisor.catalog.core.types import (
    AgentComposition,
    Artifact,
    ArtifactKind,
    MemoryProfile,
    PromptPack,
    SandboxProfile,
    SessionPins,
    ToolSet,
)
from supervisor.catalog.storage import CompositionCatalog
from supervisor.hands.keys import DEFAULT_HAND

__all__ = ["PinnedPart", "PinnedView", "pinned_view"]


@dataclass(frozen=True, slots=True)
class PinnedPart:
    kind: str
    role: str
    """``prompt``, ``tools``, ``memory``, or ``hand:<name>`` for a sandbox (``hand:default`` the main one)."""
    artifact_id: str
    digest: str
    semver: str | None
    status: str
    """``offered``, ``deprecated``, or ``unknown`` if this process cannot read it."""
    settings: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PinnedView:
    composition_id: str
    composition_digest: str
    composition_semver: str | None
    composition_status: str
    harness_version: str
    model_route: str | None
    mcp_configured: bool
    acp_configured: bool
    parts: list[PinnedPart]


def _settings(artifact: Artifact) -> dict[str, Any]:
    """The settings of a part that shape what a run can do."""
    if isinstance(artifact, PromptPack):
        return {"persona": artifact.persona, "sections": len(artifact.sections), "vars": list(artifact.vars)}
    if isinstance(artifact, ToolSet):
        return {
            "tools": [
                {"name": td.name, "tool_class": td.tool_class}
                for _, td in sorted(artifact.definitions.items())
                if td.name in artifact.tools
            ]
        }
    if isinstance(artifact, SandboxProfile):
        return {
            "provider": artifact.provider.value,
            "file_effects": artifact.file_effects.value,
            "approval_default": artifact.approval_default.value,
            "start": artifact.start,
            "idle_stop_s": artifact.idle_stop_s,
            "runner": artifact.runner,
            "browser_model": artifact.browser_model,
            "sticky_eligible": artifact.sticky_eligible,
            "image_digest": artifact.image_digest,
        }
    if isinstance(artifact, MemoryProfile):
        return {
            "compaction_enabled": artifact.compaction_enabled,
            "context_window": artifact.context_window,
            "reserve_tokens": artifact.reserve_tokens,
            "keep_recent_tokens": artifact.keep_recent_tokens,
            "pressure_ratio": artifact.pressure_ratio,
            "summarization_model": artifact.summarization_model,
            "recall_enabled": artifact.recall_enabled,
            "recall_backend": artifact.recall_backend.value,
            "recall_max_tokens": artifact.recall_max_tokens,
        }
    return {}


def _part(catalog: CompositionCatalog, kind: ArtifactKind, role: str, artifact_id: str, digest: str) -> PinnedPart:
    try:
        artifact = catalog.get(kind, artifact_id, digest)
    except CatalogError:
        return PinnedPart(kind.value, role, artifact_id, digest, None, "unknown")
    return PinnedPart(
        kind.value, role, artifact_id, digest, artifact.semver, artifact.status.value, _settings(artifact)
    )


def pinned_view(catalog: CompositionCatalog, pins: SessionPins) -> PinnedView:
    """Every part ``pins`` names, as ``catalog`` holds it (sync the composition first)."""
    parts = [
        _part(catalog, ArtifactKind.PROMPT_PACK, "prompt", pins.prompt_pack_id, pins.prompt_pack_digest),
        _part(catalog, ArtifactKind.TOOL_SET, "tools", pins.tool_set_id, pins.tool_set_digest),
    ]
    if pins.sandbox_profile_id and pins.sandbox_profile_digest:
        parts.append(
            _part(
                catalog,
                ArtifactKind.SANDBOX_PROFILE,
                f"hand:{DEFAULT_HAND}",
                pins.sandbox_profile_id,
                pins.sandbox_profile_digest,
            )
        )
    for name, profile_id, digest in pins.hands:
        parts.append(_part(catalog, ArtifactKind.SANDBOX_PROFILE, f"hand:{name}", profile_id, digest))
    if pins.memory_profile_id and pins.memory_profile_digest:
        parts.append(
            _part(catalog, ArtifactKind.MEMORY_PROFILE, "memory", pins.memory_profile_id, pins.memory_profile_digest)
        )

    try:
        comp = catalog.get_as(
            AgentComposition, ArtifactKind.COMPOSITION, pins.composition_id, pins.composition_digest
        )
        semver, status = comp.semver, comp.status.value
    except (CatalogError, TypeError):
        semver, status = None, "unknown"
    return PinnedView(
        composition_id=pins.composition_id,
        composition_digest=pins.composition_digest,
        composition_semver=semver,
        composition_status=status,
        harness_version=pins.harness_version,
        model_route=pins.model_route,
        mcp_configured=bool(pins.mcp_server_url),
        acp_configured=bool(pins.acp_base_url),
        parts=parts,
    )
