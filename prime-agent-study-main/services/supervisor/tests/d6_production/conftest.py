"""Shared helpers for independent D6 FULL-PRODUCTION acceptance."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from supervisor.catalog.bootstrap import bootstrap_composition_catalog, default_catalog_dir
from supervisor.catalog.store import CompositionCatalog

AI_REPO = Path(__file__).resolve().parents[4]
CONTRACTS = AI_REPO / "contracts"
HARNESS = AI_REPO / "harness"


@pytest.fixture
def shipped_catalog() -> CompositionCatalog:
    return bootstrap_composition_catalog(default_catalog_dir(), fail_on_verify=False)


@pytest.fixture
def coding_tools_json() -> dict[str, Any]:
    path = default_catalog_dir() / "tool_sets" / "coding-tools.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def contracts_text() -> str:
    """Concat of normative contracts that may defer coding-tools handlers."""
    chunks: list[str] = []
    for name in (
        "composition.md",
        "harness_binders.md",
        "seams.md",
        "githi_runtime.md",
        "mcp_acp.md",
    ):
        path = CONTRACTS / name
        if path.is_file():
            chunks.append(path.read_text(encoding="utf-8"))
    return "\n".join(chunks)
