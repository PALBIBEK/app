"""Offered Run compositions pin gateway plug_bindings (D5 B4)."""

from __future__ import annotations

import json
from pathlib import Path

from supervisor.catalog.bootstrap import default_catalog_dir


def test_offered_run_compositions_include_gateway_plug_bindings() -> None:
    compositions = default_catalog_dir() / "compositions"
    assert compositions.is_dir(), compositions
    offered: list[Path] = []
    missing: list[str] = []
    for path in sorted(compositions.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("status") != "offered":
            continue
        offered.append(path)
        plug = data.get("plug_bindings") or {}
        if not plug.get("gateway_base_url") or not plug.get("model_route"):
            missing.append(path.name)
    assert offered, "expected at least one offered composition under harness/compositions"
    assert not missing, (
        "offered compositions missing plug_bindings.gateway_base_url + model_route: "
        + ", ".join(missing)
    )


def test_coding_v1_defaults_echo_route_for_offline_mvp() -> None:
    path = default_catalog_dir() / "compositions" / "coding-v1.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    plug = data["plug_bindings"]
    # Catalog pin stays echo (digest-stable). Runtime upgrade: apply_runtime_model_route.
    assert plug["gateway_base_url"] == "http://model-gateway:8090"
    assert plug["model_route"] == "echo"
