"""Catalog parity verify + dump (DeepSeek dump-config parity)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from supervisor.catalog.core.digests import content_digest
from supervisor.catalog.services import require_implemented
from supervisor.catalog.storage import CompositionCatalog
from supervisor.catalog.core.types import AgentComposition


def composition_problems(catalog: CompositionCatalog, comp: AgentComposition) -> list[str]:
    """Return human-readable problems for one composition (DeepSeek compositionProblem)."""
    problems: list[str] = []
    try:
        catalog.get("prompt_pack", comp.prompt_pack.id, comp.prompt_pack.digest, require_offered=True)
    except Exception as exc:
        problems.append(f"{comp.name}: prompt_pack {comp.prompt_pack.id}@{comp.prompt_pack.digest}: {exc}")
    try:
        tool_set = catalog.get(
            "tool_set", comp.tool_set.id, comp.tool_set.digest, require_offered=True
        )
        require_implemented(tool_set)
    except Exception as exc:
        problems.append(f"{comp.name}: tool_set {comp.tool_set.id}@{comp.tool_set.digest}: {exc}")
    if comp.sandbox_profile is not None:
        sp = comp.sandbox_profile
        try:
            catalog.get("sandbox_profile", sp.id, sp.digest, require_offered=True)
        except Exception as exc:
            problems.append(f"{comp.name}: sandbox_profile {sp.id}@{sp.digest}: {exc}")
    if comp.status != "offered":
        problems.append(f"{comp.name}: status={comp.status!r} (not offered)")
    return problems


def verify_harness_catalog(
    catalog_dir: Path,
    *,
    offered_names: set[str] | None = None,
) -> list[str]:
    """Harness manifest recovery tools vs composition ToolSets."""
    from supervisor.harness.validate import verify_recovery_tools_in_compositions

    return verify_recovery_tools_in_compositions(
        catalog_dir, offered_names=offered_names
    )


def verify_composition_catalog(catalog: CompositionCatalog, catalog_dir: Path) -> list[str]:
    """Check offered compositions resolve and on-disk fixture digests match computed bodies."""
    problems: list[str] = []
    offered_names = {
        art.name
        for art in catalog.list_artifacts("composition", status="offered")
        if isinstance(art, AgentComposition)
    }
    problems.extend(verify_harness_catalog(catalog_dir, offered_names=offered_names))
    compositions_dir = catalog_dir / "compositions"
    if not compositions_dir.is_dir():
        return [f"missing compositions dir: {compositions_dir}"]

    for path in sorted(compositions_dir.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        name = raw.get("name", path.stem)
        status = raw.get("status", "offered")
        # Soft-skipped / draft fixtures must not fail catalog bootstrap.
        if name not in offered_names or status != "offered":
            continue
        digest = raw.get("digest", "")
        if digest and not digest.startswith("sha256:REPLACE"):
            try:
                comp = catalog.get("composition", name, digest, require_offered=True)
            except Exception as exc:
                problems.append(f"{path.name}: fixture digest mismatch: {exc}")
                continue
            problems.extend(composition_problems(catalog, comp))
        else:
            # No pinned composition digest in fixture — find offered row by name.
            offered = [
                art
                for (kind, art_name, _), art in catalog._by_key.items()  # noqa: SLF001
                if kind == "composition" and art_name == name and art.status == "offered"
            ]
            if not offered:
                problems.append(f"{path.name}: no offered composition named {name!r}")
                continue
            for comp in offered:
                if not isinstance(comp, AgentComposition):
                    continue
                problems.extend(composition_problems(catalog, comp))
                # Fixture child digests must match offered children when present.
                pp = raw.get("prompt_pack") or {}
                ts = raw.get("tool_set") or {}
                if pp.get("digest") and not str(pp["digest"]).startswith("sha256:REPLACE"):
                    if pp["digest"] != comp.prompt_pack.digest:
                        problems.append(
                            f"{path.name}: prompt_pack digest fixture {pp['digest']!r} "
                            f"!= offered {comp.prompt_pack.digest!r}"
                        )
                if ts.get("digest") and not str(ts["digest"]).startswith("sha256:REPLACE"):
                    if ts["digest"] != comp.tool_set.digest:
                        problems.append(
                            f"{path.name}: tool_set digest fixture {ts['digest']!r} "
                            f"!= offered {comp.tool_set.digest!r}"
                        )

    return problems


def list_catalog_problems(catalog: CompositionCatalog) -> list[str]:
    """List problems for all offered compositions in memory."""
    problems: list[str] = []
    for (kind, _name, _digest), art in catalog._by_key.items():  # noqa: SLF001
        if kind != "composition" or not isinstance(art, AgentComposition):
            continue
        if art.status != "offered":
            continue
        problems.extend(composition_problems(catalog, art))
    return problems


def dump_composition_catalog(catalog: CompositionCatalog) -> dict[str, Any]:
    """Canonical dump of offered artifacts (for CLI / startup audit)."""
    rows: list[dict[str, Any]] = []
    for (kind, name, digest), art in sorted(catalog._by_key.items()):  # noqa: SLF001
        body = art.body_for_digest()
        actual = content_digest(body)
        rows.append(
            {
                "kind": kind,
                "name": name,
                "digest": digest,
                "status": art.status,
                "computed_digest": actual,
                "digest_match": digest == actual,
                "body": body,
            }
        )
    return {"artifacts": rows, "count": len(rows)}
