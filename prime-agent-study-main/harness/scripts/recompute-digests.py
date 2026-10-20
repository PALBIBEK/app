#!/usr/bin/env python3
"""Recompute the content digests of the shipped catalog (harness/*/*.json).

Leaf artifacts first (prompt packs, tool sets, sandbox and memory profiles),
then compositions, whose references are pointed at the new leaf digests before
their own digest is computed. Run after editing any shipped artifact:

    python harness/scripts/recompute-digests.py           # rewrite the files
    python harness/scripts/recompute-digests.py --check   # CI: fail if any digest is stale

Uses the supervisor's own digest code, so a digest written here is the digest
the catalog verifies at boot.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent / "services" / "supervisor" / "src"))

from supervisor.catalog.core.digests import content_digest  # noqa: E402
from supervisor.catalog.storage import artifact_from_dict  # noqa: E402

LEAVES: tuple[tuple[str, str], ...] = (
    ("prompt_packs", "prompt_pack"),
    ("tool_sets", "tool_set"),
    ("sandbox_profiles", "sandbox_profile"),
    ("memory_profiles", "memory_profile"),
)
REFERENCES: tuple[str, ...] = ("prompt_pack", "tool_set", "sandbox_profile", "memory_profile")


def _digest(kind: str, raw: dict[str, Any]) -> str:
    body = {k: v for k, v in raw.items() if k != "digest"}
    return content_digest(artifact_from_dict(kind, body).body_for_digest())


def _render(raw: dict[str, Any]) -> str:
    return json.dumps(raw, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="report stale digests, change nothing")
    check = parser.parse_args().check

    updates: dict[Path, dict[str, Any]] = {}
    leaf_digests: dict[tuple[str, str], str] = {}
    for directory, kind in LEAVES:
        for path in sorted((ROOT / directory).glob("*.json")):
            raw = json.loads(path.read_text(encoding="utf-8"))
            digest = _digest(kind, raw)
            leaf_digests[(kind, raw["name"])] = digest
            if "digest" in raw:  # tool sets carry none: theirs is computed at load
                raw["digest"] = digest
            updates[path] = raw

    for path in sorted((ROOT / "compositions").glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        for field in REFERENCES:
            ref = raw.get(field)
            if isinstance(ref, dict) and (field, ref.get("id")) in leaf_digests:
                ref["digest"] = leaf_digests[(field, ref["id"])]
        raw["digest"] = _digest("composition", raw)
        updates[path] = raw

    stale = [p for p, raw in updates.items() if p.read_text(encoding="utf-8") != _render(raw)]
    for path in stale:
        rel = path.relative_to(ROOT)
        if check:
            print(f"stale digest: harness/{rel}")
        else:
            path.write_text(_render(updates[path]), encoding="utf-8")
            print(f"updated harness/{rel} -> {updates[path]['digest']}")
    if check and stale:
        print("run: python harness/scripts/recompute-digests.py", file=sys.stderr)
        return 1
    if not stale:
        print("shipped catalog digests are current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
