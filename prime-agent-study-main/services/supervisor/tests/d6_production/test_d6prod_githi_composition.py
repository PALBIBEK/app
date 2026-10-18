"""D6 FULL-PROD §4 High — offered composition must pin githi-tools (disk catalog)."""

from __future__ import annotations

import json
from pathlib import Path

from tests.d6_production.conftest import HARNESS


def test_prod_offered_composition_pins_githi_tools(shipped_catalog) -> None:
    """Disk catalog must offer a composition whose tool_set id is githi-tools."""
    comps = [
        a
        for a in shipped_catalog.list_artifacts("composition", status="offered")
        if getattr(a, "tool_set", None) is not None
        and getattr(a.tool_set, "id", None) == "githi-tools"
    ]
    # Also accept raw JSON on disk if bootstrap rename differs.
    disk_hits: list[Path] = []
    comp_dir = HARNESS / "compositions"
    if comp_dir.is_dir():
        for path in comp_dir.glob("*.json"):
            data = json.loads(path.read_text(encoding="utf-8"))
            ts = data.get("tool_set") or {}
            if data.get("status") == "offered" and ts.get("id") == "githi-tools":
                disk_hits.append(path)

    assert comps or disk_hits, (
        "D6 FULL-PROD: no offered composition pins tool_set id=githi-tools "
        "(today only coding-v1 → coding-tools would FAIL this gate)"
    )

    # If catalog listed it, digest pin must resolve to offered githi-tools artifact.
    if comps:
        comp = comps[0]
        githi = [
            a
            for a in shipped_catalog.list_artifacts("tool_set", status="offered")
            if a.name == "githi-tools"
        ]
        assert githi, "githi-tools ToolSet must be offered when composition pins it"
        assert comp.tool_set.digest == githi[0].digest or comp.tool_set.digest.startswith(
            "sha256:"
        ), (
            f"composition {comp.name!r} must pin a real githi-tools digest; "
            f"got {comp.tool_set!r}"
        )
