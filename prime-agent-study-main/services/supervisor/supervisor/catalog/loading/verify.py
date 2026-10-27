"""Is the catalog runnable? Every offered composition's parts exist, are offered, and can run.

Used at boot (``bootstrap``: a shipped composition with problems is not offered)
and by ``/readyz`` (``list_catalog_problems``).
"""

from __future__ import annotations

import json
from pathlib import Path

from supervisor.catalog.core.types import (
    AgentComposition,
    ArtifactKind,
    ArtifactStatus,
    DigestRef,
    ToolSet,
)
from supervisor.catalog.services import require_implemented
from supervisor.catalog.storage import SYSTEM_OWNER, CompositionCatalog


def composition_problems(catalog: CompositionCatalog, comp: AgentComposition) -> list[str]:
    """What stops ``comp`` from running, one line each (empty when it can run)."""
    problems: list[str] = []

    def check(kind: ArtifactKind, ref: DigestRef) -> None:
        try:
            artifact = catalog.get(kind, ref.id, ref.digest, require_offered=True)
            if isinstance(artifact, ToolSet):
                require_implemented(artifact)
        except Exception as exc:  # noqa: BLE001 - every failure is reported as a problem
            problems.append(f"{comp.name}: {kind.value} {ref.id}@{ref.digest}: {exc}")

    check(ArtifactKind.PROMPT_PACK, comp.prompt_pack)
    check(ArtifactKind.TOOL_SET, comp.tool_set)
    if comp.sandbox_profile is not None:
        check(ArtifactKind.SANDBOX_PROFILE, comp.sandbox_profile)
    for _, hand in comp.hands:
        check(ArtifactKind.SANDBOX_PROFILE, hand)
    if comp.memory_profile is not None:
        check(ArtifactKind.MEMORY_PROFILE, comp.memory_profile)
    if comp.status != ArtifactStatus.OFFERED:
        problems.append(f"{comp.name}: status={comp.status.value!r} (not offered)")
    return problems


def verify_composition_catalog(catalog: CompositionCatalog, catalog_dir: Path) -> list[str]:
    """Problems with the shipped catalog in ``catalog_dir`` as loaded into ``catalog``.

    Every offered composition must resolve and its recovery tools be in its tool
    set; a composition file that pins exact digests must match what was loaded.
    """
    from supervisor.harness.validate import verify_recovery_tools_in_compositions

    offered = {art.name for art in catalog.artifacts(ArtifactKind.COMPOSITION, status=ArtifactStatus.OFFERED)}
    problems = list(verify_recovery_tools_in_compositions(catalog_dir, offered_names=offered))
    compositions_dir = catalog_dir / "compositions"
    if not compositions_dir.is_dir():
        return [f"missing compositions dir: {compositions_dir}"]

    for path in sorted(compositions_dir.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        name = raw.get("name", path.stem)
        if name not in offered:  # a draft, or soft-skipped at boot
            continue
        versions = [
            art
            for art in catalog.artifacts(ArtifactKind.COMPOSITION, status=ArtifactStatus.OFFERED)
            if art.name == name and isinstance(art, AgentComposition)
        ]
        digest = raw.get("digest", "")
        if digest and not digest.startswith("sha256:REPLACE"):
            versions = [v for v in versions if v.digest == digest]
            if not versions:
                problems.append(f"{path.name}: no offered {name!r} with the file's digest {digest}")
                continue
        for comp in versions:
            problems.extend(composition_problems(catalog, comp))
            for field in ("prompt_pack", "tool_set"):
                pinned = (raw.get(field) or {}).get("digest", "")
                if pinned and not pinned.startswith("sha256:REPLACE") and pinned != getattr(comp, field).digest:
                    problems.append(
                        f"{path.name}: {field} digest {pinned!r} != offered {getattr(comp, field).digest!r}"
                    )
    return problems


def list_catalog_problems(catalog: CompositionCatalog, *, shipped_only: bool = False) -> list[str]:
    """Problems with every offered composition held (``shipped_only``: the deployment's own)."""
    problems: list[str] = []
    for comp in catalog.artifacts(ArtifactKind.COMPOSITION, status=ArtifactStatus.OFFERED):
        if not isinstance(comp, AgentComposition):
            continue
        if shipped_only and catalog.owner(ArtifactKind.COMPOSITION, comp.name) != SYSTEM_OWNER:
            continue
        problems.extend(composition_problems(catalog, comp))
    return problems
