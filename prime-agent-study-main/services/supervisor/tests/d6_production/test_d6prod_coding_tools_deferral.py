"""D6 FULL-PROD §4 High — coding-tools bash/grep/git_commit: implement OR defer in contracts."""

from __future__ import annotations

import re

from supervisor.catalog.providers.catalog_tools import CatalogToolProvider
from supervisor.seams.types import ToolInvokeContext

REQUIRED = ("bash", "grep", "git_commit")


def _name_deferred(contracts_text: str, name: str) -> bool:
    """True when contracts explicitly defer this coding-tools name."""
    text = contracts_text.lower()
    if name not in text:
        return False
    # Look for deferral markers near the corpus (composition / binders inventory).
    markers = (
        r"explicitly deferred",
        r"deferred \(not in offered",
        r"not offered until",
        r"intentionally \*\*not\*\* offered",
        r"intentionally not offered",
        r"why deferred",
        rf"`{name}`[^\n]{{0,120}}defer",
        rf"defer[^\n]{{0,80}}`{name}`",
    )
    return any(re.search(m, text) for m in markers)


def test_prod_coding_tools_bash_grep_git_commit_or_contract_deferral(
    shipped_catalog, coding_tools_json, contracts_text
) -> None:
    """For each of bash/grep/git_commit: offered+invokable XOR explicitly deferred."""
    arts = [
        a
        for a in shipped_catalog.list_artifacts("tool_set", status="offered")
        if a.name == "coding-tools"
    ]
    assert arts, "missing offered coding-tools ToolSet"
    ts = arts[0]
    listed = set(ts.tools.keys()) | set((coding_tools_json.get("tools") or {}).keys())

    sample = {
        "bash": {"command": "true"},
        "grep": {"pattern": "d6prod"},
        "git_commit": {"message": "d6prod"},
    }
    problems: list[str] = []
    for name in REQUIRED:
        if name in listed:
            result = CatalogToolProvider(ts).invoke(
                ToolInvokeContext(), name, sample[name]
            )
            if result.code == "TOOL_NOT_IMPLEMENTED":
                problems.append(
                    f"{name}: offered but TOOL_NOT_IMPLEMENTED "
                    "(implement handler or remove + defer in contracts)"
                )
        else:
            if not _name_deferred(contracts_text, name):
                problems.append(
                    f"{name}: not in offered coding-tools and contracts do not "
                    "explicitly defer it (silent MVP trim is a FAIL)"
                )
    assert not problems, "D6 FULL-PROD coding-tools:\n- " + "\n- ".join(problems)
