"""Composition resolver service — resolve compositions to session pins.

Implements CompositionResolverProtocol with:
- Protocol-based artifact access (no private attributes)
- Verified child artifact references
- Offered status enforcement

Design principles:
- Only use public Protocol methods
- Fail fast on missing/invalid artifacts
- Support both digest and id-based resolution

Reference: Refactored from resolve.py with Protocol compliance.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from supervisor.catalog.core.errors import (
    CatalogError,
    CompositionDigestRequired,
    CompositionIncomplete,
    DigestMissing,
    NotOffered,
)
from supervisor.catalog.core.types import (
    AgentComposition,
    ArtifactKind,
    ArtifactStatus,
    SessionPins,
)
from supervisor.catalog.storage.memory import reject_floating_ref

if TYPE_CHECKING:
    from supervisor.catalog.storage.composition import CompositionCatalog


__all__ = [
    "require_tenant_access",
    "resolve_composition",
]


def resolve_composition(
    catalog,
    *,
    composition_id: str | None = None,
    composition_digest: str | None = None,
    require_digest: bool = False,
) -> SessionPins:
    """Synchronous composition resolution for CompositionCatalog.

    This is a convenience function for use with the synchronous
    CompositionCatalog during bootstrap. For async stores, use
    CompositionResolver directly.

    Args:
        catalog: CompositionCatalog instance
        composition_id: Composition name (for tip resolve)
        composition_digest: Exact digest
        require_digest: Reject id-only resolve

    Returns:
        Session pins with all artifact digests

    Raises:
        CompositionIncomplete: If missing required pins
        CompositionDigestRequired: If require_digest and no digest
        NotOffered: If composition/artifact not found
    """
    if composition_id is None and composition_digest is None:
        raise CompositionIncomplete(
            composition_id="unknown",
            missing="composition_id or composition_digest",
        )

    if require_digest and composition_digest is None:
        raise CompositionDigestRequired(composition_id or "unknown")

    if composition_id is not None:
        reject_floating_ref(composition_id)
    if composition_digest is not None:
        reject_floating_ref(composition_digest)

    # Load the composition
    if composition_digest is not None:
        lookup = catalog.get_by_digest(composition_digest)
        if lookup is None:
            raise DigestMissing(
                kind=ArtifactKind.COMPOSITION.value,
                artifact_id=composition_id or "unknown",
                digest=composition_digest,
            )
        _kind, name = lookup
        if composition_id is not None and name != composition_id:
            # The digest belongs to a different artifact: never silently run it.
            raise NotOffered(
                kind=ArtifactKind.COMPOSITION.value,
                artifact_id=composition_id,
                digest=composition_digest,
            )
        comp = catalog.get(ArtifactKind.COMPOSITION, name, composition_digest)
    else:
        assert composition_id is not None
        digests = catalog.list_versions(ArtifactKind.COMPOSITION, composition_id)
        if not digests:
            raise DigestMissing(
                kind=ArtifactKind.COMPOSITION.value,
                artifact_id=composition_id,
                digest="",
            )
        # Use the latest offered version
        comp = None
        for digest in reversed(digests):
            try:
                art = catalog.get(ArtifactKind.COMPOSITION, composition_id, digest)
                if isinstance(art, AgentComposition) and art.status == ArtifactStatus.OFFERED:
                    comp = art
                    break
            except CatalogError:
                continue
        if comp is None:
            comp = catalog.get(ArtifactKind.COMPOSITION, composition_id, digests[-1])

    if not isinstance(comp, AgentComposition):
        raise NotOffered(
            kind=ArtifactKind.COMPOSITION.value,
            artifact_id=composition_id or "unknown",
            digest=composition_digest or "",
        )

    # Verify offered status
    if comp.status != ArtifactStatus.OFFERED:
        raise NotOffered(
            kind=ArtifactKind.COMPOSITION.value,
            artifact_id=comp.name,
            digest=comp.digest,
            status=comp.status.value if isinstance(comp.status, ArtifactStatus) else str(comp.status),
        )

    # Verify required child pins
    if not comp.prompt_pack.id or not comp.prompt_pack.digest:
        raise CompositionIncomplete(comp.name, "prompt_pack pin")
    if not comp.tool_set.id or not comp.tool_set.digest:
        raise CompositionIncomplete(comp.name, "tool_set pin")

    # Verify children exist
    catalog.get(
        ArtifactKind.PROMPT_PACK,
        comp.prompt_pack.id,
        comp.prompt_pack.digest,
        require_offered=True,
    )
    catalog.get(
        ArtifactKind.TOOL_SET,
        comp.tool_set.id,
        comp.tool_set.digest,
        require_offered=True,
    )

    # Handle optional sandbox profile
    sandbox_profile_id: str | None = None
    sandbox_profile_digest: str | None = None
    if (
        comp.sandbox_profile is not None
        and comp.sandbox_profile.id and comp.sandbox_profile.digest
    ):
        catalog.get(
            ArtifactKind.SANDBOX_PROFILE,
            comp.sandbox_profile.id,
            comp.sandbox_profile.digest,
            require_offered=True,
        )
        sandbox_profile_id = comp.sandbox_profile.id
        sandbox_profile_digest = comp.sandbox_profile.digest

    # Named hands: each a sandbox profile, pinned like the default one
    hands: list[tuple[str, str, str]] = []
    for hand_name, ref in comp.hands:
        catalog.get(ArtifactKind.SANDBOX_PROFILE, ref.id, ref.digest, require_offered=True)
        hands.append((hand_name, ref.id, ref.digest))

    # Handle optional memory profile
    memory_profile_id: str | None = None
    memory_profile_digest: str | None = None
    if comp.memory_profile is not None and comp.memory_profile.id and comp.memory_profile.digest:
        catalog.get(
            ArtifactKind.MEMORY_PROFILE,
            comp.memory_profile.id,
            comp.memory_profile.digest,
            require_offered=True,
        )
        memory_profile_id = comp.memory_profile.id
        memory_profile_digest = comp.memory_profile.digest

    # Apply runtime model route
    from supervisor.catalog.providers.model_route import apply_runtime_model_route

    pinned_route = comp.plug_bindings.model_route if comp.plug_bindings else None

    return SessionPins(
        composition_id=comp.name,
        composition_digest=comp.digest,
        harness_version=comp.harness_version,
        prompt_pack_id=comp.prompt_pack.id,
        prompt_pack_digest=comp.prompt_pack.digest,
        tool_set_id=comp.tool_set.id,
        tool_set_digest=comp.tool_set.digest,
        sandbox_profile_id=sandbox_profile_id,
        sandbox_profile_digest=sandbox_profile_digest,
        memory_profile_id=memory_profile_id,
        memory_profile_digest=memory_profile_digest,
        gateway_base_url=(
            comp.plug_bindings.gateway_base_url if comp.plug_bindings else None
        ),
        mcp_server_url=comp.plug_bindings.mcp_server_url if comp.plug_bindings else None,
        acp_base_url=comp.plug_bindings.acp_base_url if comp.plug_bindings else None,
        model_route=apply_runtime_model_route(pinned_route),
        hands=tuple(hands),
    )


_PINNED_PARTS: tuple[tuple[ArtifactKind, str, str], ...] = (
    (ArtifactKind.COMPOSITION, "composition_id", "composition_digest"),
    (ArtifactKind.PROMPT_PACK, "prompt_pack_id", "prompt_pack_digest"),
    (ArtifactKind.TOOL_SET, "tool_set_id", "tool_set_digest"),
    (ArtifactKind.SANDBOX_PROFILE, "sandbox_profile_id", "sandbox_profile_digest"),
    (ArtifactKind.MEMORY_PROFILE, "memory_profile_id", "memory_profile_digest"),
)


def require_tenant_access(catalog: CompositionCatalog, pins: SessionPins, tenant_id: str) -> None:
    """Every artifact ``pins`` names is shipped or ``tenant_id``'s own — else ``NotOffered``.

    Reported as not offered (404), the same as an artifact that doesn't exist,
    so one tenant cannot probe for another's artifacts.
    """
    for kind, id_field, digest_field in _PINNED_PARTS:
        name = getattr(pins, id_field)
        if name and not catalog.visible_to(kind, name, tenant_id):
            raise NotOffered(kind=kind.value, artifact_id=name, digest=getattr(pins, digest_field) or "")
    for _, profile, digest in pins.hands:
        if not catalog.visible_to(ArtifactKind.SANDBOX_PROFILE, profile, tenant_id):
            raise NotOffered(kind=ArtifactKind.SANDBOX_PROFILE.value, artifact_id=profile, digest=digest)
