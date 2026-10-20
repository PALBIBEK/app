"""C4 — telemetry_contract must not import supervisor internals (except itself)."""

from __future__ import annotations

import ast
from pathlib import Path

CONTRACT = (
    Path(__file__).resolve().parents[2]
    / "supervisor"
    / "telemetry_contract"
)

ALLOWED_PREFIXES = (
    "supervisor.telemetry_contract",
    "typing",
    "dataclasses",
    "enum",
    "abc",
    "collections",
    "functools",
    "__future__",
)


def test_contract_has_no_supervisor_internal_imports() -> None:
    banned: list[str] = []
    for path in CONTRACT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                mod = node.module
                if mod.startswith("supervisor.") and not mod.startswith(
                    "supervisor.telemetry_contract"
                ):
                    banned.append(f"{path.name}: from {mod}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("supervisor.") and not alias.name.startswith(
                        "supervisor.telemetry_contract"
                    ):
                        banned.append(f"{path.name}: import {alias.name}")
                    if alias.name == "supervisor":
                        banned.append(f"{path.name}: import supervisor")
    assert not banned, "contract imports supervisor internals:\n" + "\n".join(banned)
