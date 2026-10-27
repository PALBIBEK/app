"""Load the shipped catalog (``harness/``) into a ``CompositionCatalog``.

Layout: ``prompt_packs/``, ``tool_sets/``, ``sandbox_profiles/``, ``memory_profiles/``
and ``compositions/``, one JSON artifact per file; ``previous/`` holds the same
layout for shipped versions an edit replaced. A file whose ``status`` is
``draft`` is work in progress and is not offered.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from supervisor.catalog.core.errors import DigestMissing
from supervisor.catalog.core.types import ArtifactKind, ArtifactStatus
from supervisor.catalog.storage import CompositionCatalog, artifact_from_dict

logger = logging.getLogger(__name__)

PREVIOUS_DIR = "previous"
"""Shipped versions an edit replaced, kept frozen so sessions and tenant compositions
pinned to them still resolve. Loaded before the current versions (which stay the
latest); never rewritten by recompute-digests."""

_LEAVES: tuple[tuple[str, ArtifactKind], ...] = (
    ("prompt_packs", ArtifactKind.PROMPT_PACK),
    ("tool_sets", ArtifactKind.TOOL_SET),
    ("sandbox_profiles", ArtifactKind.SANDBOX_PROFILE),
    ("memory_profiles", ArtifactKind.MEMORY_PROFILE),
)
_REFERENCES: tuple[tuple[str, ArtifactKind], ...] = (
    ("prompt_pack", ArtifactKind.PROMPT_PACK),
    ("tool_set", ArtifactKind.TOOL_SET),
    ("sandbox_profile", ArtifactKind.SANDBOX_PROFILE),
    ("memory_profile", ArtifactKind.MEMORY_PROFILE),
)
"""Composition field -> the kind of artifact it pins."""


def load_composition_catalog(catalog_dir: Path) -> CompositionCatalog:
    """Offer every shipped artifact: every leaf a composition may pin before any composition,
    and in each, the frozen previous versions before the current ones (which stay the latest)."""
    if not catalog_dir.is_dir():
        raise FileNotFoundError(f"composition catalog dir not found: {catalog_dir}")

    catalog = CompositionCatalog()
    roots = [r for r in (catalog_dir / PREVIOUS_DIR, catalog_dir) if r.is_dir()]
    for root in roots:
        for subdir, kind in _LEAVES:
            for data in _offerable(root / subdir):
                catalog.offer(artifact_from_dict(kind, data))
    for root in roots:
        for data in _offerable(root / "compositions"):
            catalog.offer(artifact_from_dict(ArtifactKind.COMPOSITION, _pin_references(catalog, data)))
    return catalog


def _offerable(directory: Path) -> Iterator[dict[str, Any]]:
    """Each artifact file in ``directory`` that is not a draft, in name order."""
    if not directory.is_dir():
        return
    for path in sorted(directory.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("status") == ArtifactStatus.DRAFT:
            logger.info("catalog: %s is a draft; not offered", path.name)
            continue
        yield data


def _pin_references(catalog: CompositionCatalog, data: dict[str, Any]) -> dict[str, Any]:
    """``data`` with each reference pinned to an exact digest.

    A shipped composition may leave a reference's digest as a placeholder, meaning
    "the version shipped alongside": the newest offered one of that name.
    """
    pinned = dict(data)
    for field, kind in _REFERENCES:
        if (ref := data.get(field)) is not None:
            pinned[field] = _pin(catalog, kind, ref)
    if hands := data.get("hands"):
        pinned["hands"] = {name: _pin(catalog, ArtifactKind.SANDBOX_PROFILE, ref) for name, ref in hands.items()}
    return pinned


def _pin(catalog: CompositionCatalog, kind: ArtifactKind, ref: dict[str, Any]) -> dict[str, str]:
    artifact_id, digest = ref["id"], ref.get("digest") or ""
    if not _is_placeholder_digest(digest):
        return {"id": artifact_id, "digest": catalog.get(kind, artifact_id, digest).digest}
    latest = catalog.latest(kind, artifact_id)
    if latest is None:
        raise DigestMissing(kind=kind, artifact_id=artifact_id, digest=digest or "latest")
    return {"id": artifact_id, "digest": latest.digest}


def _is_placeholder_digest(digest: str) -> bool:
    """True for authoring markers and fixture image pins (all one hex digit).

    D14: shipped coding local_docker pins must not use all-identical hex digests.
    """
    d = digest.strip()
    if not d or d.startswith("sha256:REPLACE") or d == "sha256:PLACEHOLDER":
        return True
    hex_part = d.removeprefix("sha256:")
    return d.startswith("sha256:") and len(hex_part) == 64 and len(set(hex_part)) == 1
