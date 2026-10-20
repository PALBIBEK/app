"""Layering checks: a part of the supervisor that must not reach into the rest of it."""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "supervisor"


def imports_outside(package: str) -> list[str]:
    """Imports in ``package`` (e.g. ``supervisor.gateway``) of supervisor modules outside it."""
    root = PACKAGE_ROOT.joinpath(*package.split(".")[1:])
    assert root.is_dir(), f"missing package {package} at {root}"
    offenders: list[str] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names = (
                [a.name for a in node.names] if isinstance(node, ast.Import)
                else [node.module or ""] if isinstance(node, ast.ImportFrom) and node.level == 0
                else []
            )
            for name in names:
                inside = name == package or name.startswith(package + ".")
                if (name == "supervisor" or name.startswith("supervisor.")) and not inside:
                    offenders.append(f"{path.relative_to(PACKAGE_ROOT)}:{node.lineno}: {name}")
    return offenders
