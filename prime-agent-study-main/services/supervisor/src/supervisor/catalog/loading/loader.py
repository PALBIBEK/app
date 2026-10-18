"""Load shipped composition catalog artifacts from the harness tree."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from supervisor.catalog.core.errors import DigestMissing
from supervisor.catalog.storage import (
    CompositionCatalog,
    memory_profile_from_dict,
    prompt_pack_from_dict,
    sandbox_profile_from_dict,
    tool_set_from_dict,
)
from supervisor.catalog.core.types import AgentComposition, ArtifactKind, DigestRef, PlugBindings


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_dir(catalog_dir: Path, subdir: str, loader) -> list[Any]:  # type: ignore[no-untyped-def]
    root = catalog_dir / subdir
    if not root.is_dir():
        return []
    items = []
    for path in sorted(root.glob("*.json")):
        items.append(loader(_read_json(path)))
    return items


def load_composition_catalog(catalog_dir: Path) -> CompositionCatalog:
    """Offer all shipped artifacts from ``catalog_dir`` (prompts → tools → profiles → compositions)."""
    if not catalog_dir.is_dir():
        raise FileNotFoundError(f"composition catalog dir not found: {catalog_dir}")

    catalog = CompositionCatalog()
    for pack in _load_dir(catalog_dir, "prompt_packs", prompt_pack_from_dict):
        catalog.offer(pack)
    for tool_set in _load_dir(catalog_dir, "tool_sets", tool_set_from_dict):
        catalog.offer(tool_set)
    for profile in _load_dir(catalog_dir, "sandbox_profiles", sandbox_profile_from_dict):
        catalog.offer(profile)
    for profile in _load_dir(catalog_dir, "memory_profiles", memory_profile_from_dict):
        catalog.offer(profile)

    for path in sorted((catalog_dir / "compositions").glob("*.json")):
        data = _read_json(path)
        pp_raw = data["prompt_pack"]
        ts_raw = data["tool_set"]
        sp_raw = data.get("sandbox_profile")
        mp_raw = data.get("memory_profile")
        pp = _resolve_ref(catalog, "prompt_pack", pp_raw["id"], pp_raw.get("digest", ""))
        ts = _resolve_ref(catalog, "tool_set", ts_raw["id"], ts_raw.get("digest", ""))
        sp_ref: DigestRef | None = None
        if sp_raw is not None:
            sp = _resolve_ref(
                catalog, "sandbox_profile", sp_raw["id"], sp_raw.get("digest", "")
            )
            sp_ref = DigestRef(id=sp.name, digest=sp.digest)
        mp_ref: DigestRef | None = None
        if mp_raw is not None:
            mp = _resolve_ref(
                catalog, "memory_profile", mp_raw["id"], mp_raw.get("digest", "")
            )
            mp_ref = DigestRef(id=mp.name, digest=mp.digest)
        comp = AgentComposition(
            name=data["name"],
            semver=data["semver"],
            digest=data.get("digest", ""),
            protocol_version=int(data.get("protocol_version", 1)),
            status=data.get("status", "offered"),  # type: ignore[arg-type]
            harness_version=data["harness_version"],
            prompt_pack=DigestRef(id=pp.name, digest=pp.digest),
            tool_set=DigestRef(id=ts.name, digest=ts.digest),
            sandbox_profile=sp_ref,
            memory_profile=mp_ref,
            plug_bindings=PlugBindings.from_dict(data.get("plug_bindings")),
        )
        catalog.offer(comp)

    return catalog


def _is_placeholder_digest(digest: str) -> bool:
    """True for composition REPLACE markers and fixture image pins (aaaa…/bbbb…).

    D14: shipped coding local_docker pins must not use all-identical hex digests.
    """
    if not digest:
        return True
    d = digest.strip()
    if d.startswith("sha256:REPLACE") or d == "sha256:PLACEHOLDER":
        return True
    # sha256:<64 hex> where every hex digit is the same → fixture, not a real image
    if d.startswith("sha256:") and len(d) == len("sha256:") + 64:
        hex_part = d[len("sha256:") :]
        if all(c in "0123456789abcdef" for c in hex_part) and len(set(hex_part)) == 1:
            return True
    return False


def _resolve_ref(
    catalog: CompositionCatalog,
    kind: ArtifactKind,
    artifact_id: str,
    digest: str,
):
    if not _is_placeholder_digest(digest):
        return catalog.get(kind, artifact_id, digest)
    digests = catalog._by_name.get((kind, artifact_id), [])  # noqa: SLF001
    if not digests:
        raise DigestMissing(f"unknown {kind} id={artifact_id!r} (no offered digest)")
    return catalog.get(kind, artifact_id, digests[-1])
