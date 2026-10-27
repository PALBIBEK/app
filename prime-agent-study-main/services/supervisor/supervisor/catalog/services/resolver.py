"""A composition, resolved once at session creation into the exact versions the session runs.

``resolve_composition`` checks every part is offered and returns ``SessionPins``,
which the session keeps for its whole life: republishing or deprecating the
composition afterwards changes nothing for a session already pinned.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from supervisor.catalog.core.digests import reject_floating_ref
from supervisor.catalog.core.errors import (
    CompositionDigestRequired,
    CompositionIncompatible,
    CompositionIncomplete,
    DigestMissing,
    NotOffered,
)
from supervisor.catalog.core.types import (
    AgentComposition,
    ArtifactKind,
    ArtifactStatus,
    SandboxProfile,
    SessionPins,
)
from supervisor.catalog.providers.model_route import apply_runtime_model_route

if TYPE_CHECKING:
    from supervisor.catalog.storage.composition import CompositionCatalog


__all__ = [
    "composition_digest_required",
    "require_sticky_compatible",
    "require_tenant_access",
    "resolve_composition",
]


def composition_digest_required() -> bool:
    """``REQUIRE_COMPOSITION_DIGEST``: sessions must name the exact composition version.

    Off by default (a name resolves to its newest offered version); a deployment that
    must be able to say exactly what every session ran turns it on.
    """
    return os.environ.get("REQUIRE_COMPOSITION_DIGEST", "").strip().lower() in ("1", "true", "yes")


def resolve_composition(
    catalog: CompositionCatalog,
    *,
    composition_id: str | None = None,
    composition_digest: str | None = None,
    require_digest: bool = False,
) -> SessionPins:
    """The pins for ``composition_id`` at ``composition_digest`` (or its newest offered version).

    ``require_digest`` refuses a name without a digest (``CompositionDigestRequired``):
    a deployment that sets ``REQUIRE_COMPOSITION_DIGEST`` runs only versions a caller
    named exactly. A floating reference (``latest``) is always refused.
    """
    if composition_id is None and composition_digest is None:
        raise CompositionIncomplete("unknown", "composition_id or composition_digest")
    if require_digest and composition_digest is None:
        raise CompositionDigestRequired(composition_id or "unknown")
    for ref in (composition_id, composition_digest):
        if ref is not None:
            reject_floating_ref(ref)

    comp = (
        _by_digest(catalog, composition_id, composition_digest)
        if composition_digest is not None
        else _newest_offered(catalog, composition_id or "")
    )
    if comp.status != ArtifactStatus.OFFERED:
        raise NotOffered(
            kind=ArtifactKind.COMPOSITION.value, artifact_id=comp.name, digest=comp.digest, status=comp.status.value
        )
    if not comp.prompt_pack.id or not comp.prompt_pack.digest:
        raise CompositionIncomplete(comp.name, "prompt_pack pin")
    if not comp.tool_set.id or not comp.tool_set.digest:
        raise CompositionIncomplete(comp.name, "tool_set pin")

    plug = comp.plug_bindings
    pins = SessionPins(
        composition_id=comp.name,
        composition_digest=comp.digest,
        harness_version=comp.harness_version,
        prompt_pack_id=comp.prompt_pack.id,
        prompt_pack_digest=comp.prompt_pack.digest,
        tool_set_id=comp.tool_set.id,
        tool_set_digest=comp.tool_set.digest,
        sandbox_profile_id=comp.sandbox_profile.id if comp.sandbox_profile else None,
        sandbox_profile_digest=comp.sandbox_profile.digest if comp.sandbox_profile else None,
        memory_profile_id=comp.memory_profile.id if comp.memory_profile else None,
        memory_profile_digest=comp.memory_profile.digest if comp.memory_profile else None,
        gateway_base_url=plug.gateway_base_url if plug else None,
        mcp_server_url=plug.mcp_server_url if plug else None,
        acp_base_url=plug.acp_base_url if plug else None,
        model_route=apply_runtime_model_route(plug.model_route if plug else None),
        hands=tuple((name, ref.id, ref.digest) for name, ref in comp.hands),
    )
    # Every part a session will bind must be offered now; the composition itself was checked above.
    for kind, artifact_id, digest in pins.refs():
        if kind is not ArtifactKind.COMPOSITION:
            catalog.get(kind, artifact_id, digest, require_offered=True)
    return pins


def _by_digest(catalog: CompositionCatalog, composition_id: str | None, digest: str) -> AgentComposition:
    found = catalog.get_by_digest(digest)
    if found is None:
        raise DigestMissing(kind=ArtifactKind.COMPOSITION.value, artifact_id=composition_id or "unknown", digest=digest)
    _, name = found
    if composition_id is not None and name != composition_id:
        # The digest belongs to a different artifact: never silently run it.
        raise NotOffered(kind=ArtifactKind.COMPOSITION.value, artifact_id=composition_id, digest=digest)
    return catalog.get_as(AgentComposition, ArtifactKind.COMPOSITION, name, digest)


def _newest_offered(catalog: CompositionCatalog, composition_id: str) -> AgentComposition:
    """The newest offered version (else the newest, which the caller then refuses as not offered)."""
    versions = catalog.list_versions(ArtifactKind.COMPOSITION, composition_id)
    if not versions:
        raise DigestMissing(kind=ArtifactKind.COMPOSITION.value, artifact_id=composition_id, digest="")
    for digest in reversed(versions):
        comp = catalog.get_as(AgentComposition, ArtifactKind.COMPOSITION, composition_id, digest)
        if comp.status == ArtifactStatus.OFFERED:
            return comp
    return catalog.get_as(AgentComposition, ArtifactKind.COMPOSITION, composition_id, versions[-1])


def require_tenant_access(catalog: CompositionCatalog, pins: SessionPins, tenant_id: str) -> None:
    """Every artifact ``pins`` names is shipped or ``tenant_id``'s own — else ``NotOffered``.

    Reported as not offered (404), the same as an artifact that doesn't exist,
    so one tenant cannot probe for another's artifacts.
    """
    for kind, name, digest in pins.refs():
        if not catalog.visible_to(kind, name, tenant_id):
            raise NotOffered(kind=kind.value, artifact_id=name, digest=digest)


def require_sticky_compatible(
    catalog: CompositionCatalog,
    composition: AgentComposition,
    *,
    sticky: bool,
) -> None:
    """``CompositionIncompatible`` if a sticky harness pins a sandbox that may not be kept.

    A sticky harness keeps a session's sandboxes between turns; a sandbox profile
    with ``sticky_eligible: false`` must be torn down after each one. Checked when a
    composition is published (against its harness version) and when a session is
    created, so the two can never run together.
    """
    if not sticky:
        return
    refs = [ref for ref in (composition.sandbox_profile,) if ref is not None]
    refs += [ref for _, ref in composition.hands]
    for ref in refs:
        profile = catalog.get_as(SandboxProfile, ArtifactKind.SANDBOX_PROFILE, ref.id, ref.digest)
        if not profile.sticky_eligible:
            raise CompositionIncompatible(
                composition.name,
                f"harness {composition.harness_version!r} keeps its sandbox between turns, but sandbox "
                f"profile {profile.name!r} may not be kept (sticky_eligible: false)",
            )
